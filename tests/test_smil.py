import random

import pytest
from lxml import etree

from conftest import build_epub, make_tracks
from readalign.align import TimedSentence
from readalign.clock import parse_clock
from readalign.epub import load_epub
from readalign.errors import InputError
from readalign.smil import SMIL_NS, build_smil, continuous_clips, overlay_duration


def test_continuous_clips_preserve_adjacent_clips_and_outer_boundaries(tmp_path):
    package = load_epub(build_epub(str(tmp_path / "input.epub")))
    sentences = package.docs[0].sentences
    tracks = make_tracks([20])
    entries = [TimedSentence(sentences[0], 2, 4, 1.0, 1),
               TimedSentence(sentences[1], 4, 7, 1.0, 1)]
    assert continuous_clips(entries, tracks) == entries
    assert continuous_clips([], tracks) == []
    assert continuous_clips(entries[:1], tracks) == entries[:1]


def test_gap_ending_at_track_boundary_does_not_add_zero_length_clip(tmp_path):
    package = load_epub(build_epub(str(tmp_path / "input.epub")))
    sentences = package.docs[0].sentences
    tracks = make_tracks([10, 10])
    entries = [TimedSentence(sentences[0], 2, 4, 1.0, 1),
               TimedSentence(sentences[1], 10, 12, 1.0, 2)]
    clips = continuous_clips(entries, tracks)
    assert [(entry.start, entry.end, entry.track_index) for entry in clips] == [
        (2, 10, 1), (10, 12, 2)]


def test_playback_does_not_repeat_overlapping_alignment_audio(tmp_path):
    package = load_epub(build_epub(str(tmp_path / "input.epub")))
    sentences = package.docs[0].sentences
    tracks = make_tracks([20])
    entries = [TimedSentence(sentences[0], 2, 6, 1.0, 1),
               TimedSentence(sentences[1], 4, 7, 1.0, 1)]
    clips = continuous_clips(entries, tracks)
    assert [(entry.start, entry.end) for entry in clips] == [(2, 4), (4, 7)]
    assert entries[0].end == 6


def test_submillisecond_boundary_never_serializes_an_empty_clip(tmp_path):
    package = load_epub(build_epub(str(tmp_path / "input.epub")))
    sentences = package.docs[0].sentences
    tracks = make_tracks([10, 10])
    entries = [TimedSentence(sentences[0], 2, 4, 1.0, 1),
               TimedSentence(sentences[1], 10.0001, 12, 1.0, 2)]
    clips = continuous_clips(entries, tracks)
    xml = build_smil("EPUB/test.smil", "EPUB/chapter1.xhtml", clips,
                     {track.index: track for track in tracks},
                     {track.index: track.href for track in tracks})
    audio = list(etree.fromstring(xml).iter(f"{{{SMIL_NS}}}audio"))
    assert len(audio) == 2
    assert all(parse_clock(item.get("clipEnd")) > parse_clock(item.get("clipBegin"))
               for item in audio)


@pytest.mark.parametrize("bad_start", [1, float("nan"), float("inf")])
def test_invalid_playback_order_or_timestamps_fail_clearly(tmp_path, bad_start):
    package = load_epub(build_epub(str(tmp_path / "input.epub")))
    sentences = package.docs[0].sentences
    tracks = make_tracks([20])
    entries = [TimedSentence(sentences[0], 2, 4, 1.0, 1),
               TimedSentence(sentences[1], bad_start, 7, 1.0, 1)]
    with pytest.raises(InputError):
        continuous_clips(entries, tracks)


def test_overlay_duration_matches_serialized_file_local_clocks(tmp_path):
    package = load_epub(build_epub(str(tmp_path / "input.epub")))
    tracks = make_tracks([10])
    entries = [TimedSentence(package.docs[0].sentences[0], 1.0004, 1.0006, 1.0, 1)]
    by_index = {1: tracks[0]}
    xml = build_smil("EPUB/test.smil", "EPUB/chapter1.xhtml", entries,
                     by_index, {1: tracks[0].href})
    audio = etree.fromstring(xml).find(f".//{{{SMIL_NS}}}audio")
    serialized = parse_clock(audio.get("clipEnd")) - parse_clock(audio.get("clipBegin"))
    assert overlay_duration(entries, by_index) == pytest.approx(serialized)


def test_duplicate_starts_do_not_replay_audio_or_emit_empty_highlights(tmp_path):
    package = load_epub(build_epub(str(tmp_path / "input.epub")))
    sentences = package.docs[0].sentences
    tracks = make_tracks([10])
    entries = [TimedSentence(sentences[0], 2, 4, 1.0, 1),
               TimedSentence(sentences[1], 2, 5, 1.0, 1)]
    assert continuous_clips(entries, tracks) == entries[1:]


@pytest.mark.parametrize("fault", ["unknown", "duplicate", "gap", "nonfinite", "outside"])
def test_invalid_track_timeline_fails_instead_of_silently_dropping_audio(tmp_path, fault):
    package = load_epub(build_epub(str(tmp_path / "input.epub")))
    tracks = make_tracks([10, 10])
    entry = TimedSentence(package.docs[0].sentences[0], 2, 4, 1.0, 1)
    if fault == "unknown":
        entry.track_index = 99
    elif fault == "duplicate":
        tracks[1].index = 1
    elif fault == "gap":
        tracks[1].offset += 1
    elif fault == "nonfinite":
        tracks[1].duration = float("inf")
    else:
        entry.end = 11
    with pytest.raises(InputError):
        continuous_clips([entry], tracks)


def test_randomized_file_local_clocks_are_contiguous_and_match_metadata(tmp_path):
    package = load_epub(build_epub(str(tmp_path / "input.epub")))
    sentence = package.docs[0].sentences[0]
    rng = random.Random(3)
    for _ in range(100):
        tracks = make_tracks([rng.uniform(5, 30) for _ in range(3)])
        by_index = {track.index: track for track in tracks}
        starts = sorted(rng.uniform(0.1, tracks[-1].end - 0.1) for _ in range(30))
        entries = []
        for start in starts:
            track = next(track for track in tracks if start < track.end)
            entries.append(TimedSentence(sentence, start, min(start + 0.05, track.end),
                                         1.0, track.index))
        clips = continuous_clips(entries, tracks)
        xml = build_smil("EPUB/test.smil", "EPUB/chapter1.xhtml", clips, by_index,
                         {track.index: track.href for track in tracks})
        previous = None
        milliseconds = 0
        for audio, clip in zip(etree.fromstring(xml).iter(f"{{{SMIL_NS}}}audio"),
                               clips, strict=True):
            begin = round(parse_clock(audio.get("clipBegin")) * 1000)
            end = round(parse_clock(audio.get("clipEnd")) * 1000)
            track = by_index[clip.track_index]
            assert 0 <= begin < end <= round(track.duration * 1000)
            if previous is not None:
                last_track, last_end = previous
                if last_track == track.index:
                    assert begin == last_end
                else:
                    assert last_end == round(by_index[last_track].duration * 1000)
                    assert begin == 0
            previous = track.index, end
            milliseconds += end - begin
        assert overlay_duration(clips, by_index) == milliseconds / 1000
