"""Sidecars and the report: the parts a user reads to decide whether to trust the build."""

import json

from conftest import build_epub, make_tracks, speak
from readalign.align import align
from readalign.epub import annotate_document, load_epub
from readalign.report import build_report, document_stats, format_summary
from readalign.sidecar import build_align_json, build_vtt, vtt_timestamp, write_sidecars


def prepared(tmp_path):
    package = load_epub(build_epub(str(tmp_path / "in.epub")))
    sentences = []
    for index, doc in enumerate(package.docs):
        doc.sentences = annotate_document(doc.tree, index)
        sentences.extend(doc.sentences)
    words = speak(sentences)
    tracks = make_tracks([words[-1].end + 2.0])
    return package, align(sentences, words, tracks, log=lambda *_: None), tracks


def test_vtt_timestamp_pads_the_hour():
    assert vtt_timestamp(0) == "00:00:00.000"
    assert vtt_timestamp(3661.5) == "01:01:01.500"


def test_vtt_is_in_track_time_not_book_time(tmp_path):
    _, result, tracks = prepared(tmp_path)
    tracks[0].offset = 1000.0
    for entry in result.timed:
        entry.start += 1000.0
        entry.end += 1000.0
    text = build_vtt(result.timed, tracks[0], "audio/part001.mp3")
    assert text.startswith("WEBVTT")
    assert "NOTE audio audio/part001.mp3" in text
    assert text.splitlines()[5].startswith("00:00:0")
    assert text.count("-->") == len(result.timed)


def test_align_json_has_one_row_per_timed_sentence(tmp_path):
    package, result, tracks = prepared(tmp_path)
    payload = json.loads(
        build_align_json(
            result,
            {track.index: track for track in tracks},
            {index: doc.href for index, doc in enumerate(package.docs)},
            {1: "audio/part001.mp3"},
            title=package.title(),
        )
    )
    assert payload["title"] == "A Test Voyage"
    assert payload["sentences"] == len(result.timed)
    row = payload["alignment"][0]
    assert row["id"] == "ra-1-1"
    assert row["document"] == "chapter1.xhtml"
    assert row["audio"] == "audio/part001.mp3"
    assert row["clip_end"] > row["clip_begin"]


def test_write_sidecars_writes_one_vtt_per_document(tmp_path):
    package, result, tracks = prepared(tmp_path)
    written = write_sidecars(
        str(tmp_path / "side"), result, tracks,
        {index: doc.href for index, doc in enumerate(package.docs)},
        {1: "audio/part001.mp3"},
    )
    names = sorted(path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1] for path in written)
    assert names == ["align.json", "chapter1.vtt", "chapter2.vtt"]


def test_report_counts_and_drift_gate(tmp_path):
    package, result, tracks = prepared(tmp_path)
    stats = document_stats(package, result)
    report = build_report(
        package, result, tracks, stats,
        output=str(tmp_path / "out.epub"),
        asr_info={"device": "cpu", "model": "tiny"},
        drift_threshold=2.5,
        warnings=[],
    )
    assert report["coverage"]["sentences"] == sum(len(doc.sentences) for doc in package.docs)
    assert report["coverage"]["aligned"] == len(result.timed)
    assert report["drift"]["within_threshold"]
    assert report["documents_without_overlay"] == []
    assert len(report["documents"]) == len(package.docs)


def test_report_flags_drift_over_the_threshold(tmp_path):
    package, result, tracks = prepared(tmp_path)
    result.timed[2].drift = 9.0
    report = build_report(
        package, result, tracks, document_stats(package, result),
        output=str(tmp_path / "out.epub"),
        asr_info={},
        drift_threshold=2.5,
        warnings=["something to say"],
    )
    assert not report["drift"]["within_threshold"]
    assert report["drift"]["max"] == 9.0
    assert "something to say" in format_summary(report)


def test_report_names_documents_with_no_overlay(tmp_path):
    package, result, tracks = prepared(tmp_path)
    result.timed = [entry for entry in result.timed if entry.sentence.doc_index == 0]
    stats = document_stats(package, result)
    report = build_report(
        package, result, tracks, stats,
        output=str(tmp_path / "out.epub"),
        asr_info={},
        drift_threshold=2.5,
        warnings=[],
    )
    assert report["documents_without_overlay"] == ["chapter2.xhtml"]
    assert "no overlay" in format_summary(report)
