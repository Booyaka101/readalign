"""The shipped artefact: SMIL structure, the rewritten OPF, and the OCF archive itself."""

import os
import shutil
import subprocess
import zipfile

import pytest
from lxml import etree

from conftest import build_epub, make_tracks, speak
from readalign.align import align
from readalign.cli import check_epub
from readalign.epub import OPF_NS, load_epub
from readalign.package import ACTIVE_CLASS, build_output, ensure_mimetype, write_epub
from readalign.smil import SMIL_NS

has_ffmpeg = shutil.which("ffmpeg") and shutil.which("ffprobe")


def silent_mp3(path, seconds):
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
         "-i", "anullsrc=r=22050:cl=mono", "-t", str(seconds), "-c:a", "libmp3lame", str(path)],
        check=True,
    )
    return str(path)


@pytest.fixture
def aligned(tmp_path):
    """A real EPUB plus a real (silent) MP3, timed with a synthetic transcript."""
    package = load_epub(build_epub(str(tmp_path / "in.epub")))
    sentences = [sentence for doc in package.docs for sentence in doc.sentences]
    words = speak(sentences)
    duration = words[-1].end + 2.0
    tracks = make_tracks([duration])
    if has_ffmpeg:
        tracks[0].path = silent_mp3(tmp_path / "part001.mp3", duration)
    result = align(sentences, words, tracks, log=lambda *_: None)
    entries = {}
    for entry in result.timed:
        entries.setdefault(package.docs[entry.sentence.doc_index].manifest_id, []).append(entry)
    return package, result, tracks, entries


def test_smil_documents_are_well_formed(aligned):
    package, _, tracks, entries = aligned
    if not has_ffmpeg:
        pytest.skip("ffmpeg is needed to embed real audio")
    files, summary = build_output(package, entries, tracks)
    smils = [name for name in files if name.endswith(".smil")]
    assert len(smils) == summary["overlays"] == 2
    root = etree.fromstring(files[smils[0]])
    assert root.tag == f"{{{SMIL_NS}}}smil"
    assert root.get("version") == "3.0"
    body = root.find(f"{{{SMIL_NS}}}body")
    assert body.get("{http://www.idpf.org/2007/ops}textref").endswith("chapter1.xhtml")
    par = body.find(f"{{{SMIL_NS}}}par")
    assert par.find(f"{{{SMIL_NS}}}text").get("src").endswith("#ra-1-1")
    assert par.find(f"{{{SMIL_NS}}}audio").get("clipBegin").count(":") == 2


def test_skippable_content_is_wrapped_in_a_seq(aligned):
    package, _, tracks, entries = aligned
    if not has_ffmpeg:
        pytest.skip("ffmpeg is needed to embed real audio")
    files, _ = build_output(package, entries, tracks)
    chapter2 = next(name for name in files if name.endswith("chapter2.smil"))
    root = etree.fromstring(files[chapter2])
    seqs = root.findall(f".//{{{SMIL_NS}}}seq")
    assert len(seqs) == 1
    assert seqs[0].get("{http://www.idpf.org/2007/ops}type") == "footnote"
    assert seqs[0].get("{http://www.idpf.org/2007/ops}textref").endswith("#fn1")


def test_package_document_carries_the_overlay_metadata(aligned):
    package, _, tracks, entries = aligned
    if not has_ffmpeg:
        pytest.skip("ffmpeg is needed to embed real audio")
    files, _ = build_output(package, entries, tracks, narrator="A Narrator")
    opf = etree.fromstring(files[package.opf_path])
    items = {item.get("id"): item for item in opf.iter(f"{{{OPF_NS}}}item")}
    assert items["c1"].get("media-overlay") in items
    assert items[items["c1"].get("media-overlay")].get("media-type") == "application/smil+xml"
    metas = [
        (meta.get("property"), meta.get("refines"), meta.text)
        for meta in opf.iter(f"{{{OPF_NS}}}meta")
    ]
    durations = [meta for meta in metas if meta[0] == "media:duration"]
    assert len(durations) == 3  # one per overlay plus the unrefined total
    assert sum(1 for meta in durations if meta[1] is None) == 1
    assert ("media:active-class", None, ACTIVE_CLASS) in metas
    assert ("media:narrator", None, "A Narrator") in metas


def test_archive_starts_with_a_stored_mimetype(aligned, tmp_path):
    package, _, tracks, entries = aligned
    if not has_ffmpeg:
        pytest.skip("ffmpeg is needed to embed real audio")
    files, _ = build_output(package, entries, tracks)
    ensure_mimetype(files)
    out = str(tmp_path / "out.epub")
    write_epub(out, files, package.order)
    with zipfile.ZipFile(out) as archive:
        first = archive.infolist()[0]
        assert first.filename == "mimetype"
        assert first.compress_type == zipfile.ZIP_STORED
        assert archive.read("mimetype") == b"application/epub+zip"


@pytest.mark.skipif(not has_ffmpeg, reason="ffmpeg and ffprobe are needed")
def test_check_accepts_what_build_wrote(aligned, tmp_path):
    package, result, tracks, entries = aligned
    files, _ = build_output(package, entries, tracks)
    ensure_mimetype(files)
    out = str(tmp_path / "checked.epub")
    write_epub(out, files, package.order)
    findings = check_epub(out)
    assert findings["errors"] == []
    assert findings["ok"]
    assert findings["sentences"] == len(result.timed)


def _clip_past_the_end(root):
    root.find(f".//{{{SMIL_NS}}}audio").set("clipEnd", "9:59:59.000")


def _dangling_fragment(root):
    text = root.find(f".//{{{SMIL_NS}}}text")
    text.set("src", text.get("src").split("#")[0] + "#ra-does-not-exist")


@pytest.mark.skipif(not has_ffmpeg, reason="ffmpeg and ffprobe are needed")
@pytest.mark.parametrize(
    ("break_it", "expected"),
    [
        (_clip_past_the_end, "past the file's real duration"),
        (_dangling_fragment, "ra-does-not-exist"),
    ],
)
def test_check_reports_a_broken_overlay(aligned, tmp_path, break_it, expected):
    package, _, tracks, entries = aligned
    files, _ = build_output(package, entries, tracks)
    ensure_mimetype(files)
    smil_name = next(name for name in files if name.endswith(".smil"))
    root = etree.fromstring(files[smil_name])
    break_it(root)
    files[smil_name] = etree.tostring(root, xml_declaration=True, encoding="utf-8")
    out = str(tmp_path / "broken.epub")
    write_epub(out, files, package.order)
    findings = check_epub(out)
    assert not findings["ok"]
    assert any(expected in error for error in findings["errors"])


def test_check_refuses_something_that_is_not_an_epub(tmp_path):
    from readalign.errors import InputError

    path = tmp_path / "plain.epub"
    path.write_bytes(b"not a zip at all")
    with pytest.raises(InputError):
        check_epub(str(path))


@pytest.mark.skipif(
    not os.environ.get("READALIGN_MO_TESTBOOK"),
    reason="set READALIGN_MO_TESTBOOK to the epubtest.org Media Overlays test book",
)
def test_checker_accepts_the_official_media_overlays_test_book():
    """Guards the checker itself: it must pass a book someone else built to the spec."""
    findings = check_epub(os.environ["READALIGN_MO_TESTBOOK"])
    assert findings["errors"] == []
    assert findings["overlays"] > 0
