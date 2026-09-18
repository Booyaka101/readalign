"""Alignment behaviour on transcripts that are deliberately wrong in realistic ways."""

import pytest

from conftest import build_epub, make_tracks, speak
from readalign.align import align, align_tokens
from readalign.epub import load_epub
from readalign.errors import AlignmentError


def book(tmp_path, name="align.epub", chapters=None):
    path = build_epub(str(tmp_path / name), **({"chapters": chapters} if chapters else {}))
    package = load_epub(path)
    return package, [sentence for doc in package.docs for sentence in doc.sentences]


def test_perfect_transcript_times_everything(tmp_path):
    _, sentences = book(tmp_path)
    words = speak(sentences)
    result = align(sentences, words, make_tracks([600.0]), log=lambda *_: None)
    assert len(result.timed) == len(sentences)
    assert max(entry.drift for entry in result.timed) < 0.5
    assert all(entry.confidence == 1.0 for entry in result.timed)


def test_timings_are_monotonic(tmp_path):
    _, sentences = book(tmp_path)
    result = align(sentences, speak(sentences), make_tracks([600.0]), log=lambda *_: None)
    moments = [(entry.start, entry.end) for entry in result.timed]
    assert moments == sorted(moments)
    assert all(end > start for start, end in moments)


def test_narrator_front_matter_shifts_the_book(tmp_path):
    """A LibriVox intro has no counterpart in the ebook and must not drag timings backwards."""
    _, sentences = book(tmp_path)
    intro = ["This is a LibriVox recording all LibriVox recordings are in the public domain"]
    result = align(sentences, speak(sentences, lead_in=intro), make_tracks([600.0]),
                   log=lambda *_: None)
    assert len(result.timed) == len(sentences)
    assert result.timed[0].start > 3.0


def test_book_front_matter_with_no_narration_is_trimmed(tmp_path):
    _, sentences = book(tmp_path)
    narrated = sentences[3:]
    result = align(sentences, speak(narrated), make_tracks([600.0]), log=lambda *_: None)
    assert result.trimmed_head == 3
    assert {entry.sentence.fragment_id for entry in result.timed} == {
        sentence.fragment_id for sentence in narrated
    }


def test_a_skipped_passage_is_interpolated_not_dropped(tmp_path):
    _, sentences = book(tmp_path)
    spoken = sentences[:2] + sentences[4:]
    result = align(sentences, speak(spoken), make_tracks([600.0]), log=lambda *_: None)
    timed = {entry.sentence.fragment_id: entry for entry in result.timed}
    assert sentences[2].fragment_id in timed
    assert timed[sentences[2].fragment_id].interpolated


def test_clip_end_is_clamped_to_the_track(tmp_path):
    _, sentences = book(tmp_path)
    words = speak(sentences)
    tracks = make_tracks([words[-1].end - 1.0])
    result = align(sentences, words, tracks, log=lambda *_: None)
    assert result.timed[-1].end <= tracks[-1].end
    assert any(entry.clamped for entry in result.timed)


def test_sentences_are_assigned_to_the_right_track(tmp_path):
    _, sentences = book(tmp_path)
    words = speak(sentences)
    half = words[len(words) // 2].start
    result = align(sentences, words, make_tracks([half, 600.0]), log=lambda *_: None)
    assert {entry.track_index for entry in result.timed} == {1, 2}
    for entry in result.timed:
        assert entry.track_index == (1 if entry.start < half else 2)


def test_no_sentences_is_a_clear_error():
    with pytest.raises(AlignmentError, match="no sentences"):
        align([], [], make_tracks([10.0]), log=lambda *_: None)


def test_silent_audio_is_a_clear_error(tmp_path):
    _, sentences = book(tmp_path)
    with pytest.raises(AlignmentError, match="no words"):
        align(sentences, [], make_tracks([10.0]), log=lambda *_: None)


def test_a_different_book_is_refused(tmp_path):
    _, sentences = book(tmp_path)
    from readalign.asr import Word

    spoken = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot"] * 40
    noise = [Word(token, i * 0.3, i * 0.3 + 0.3, 0.9) for i, token in enumerate(spoken)]
    with pytest.raises(AlignmentError, match="matched the ebook"):
        align(sentences, noise, make_tracks([600.0]), log=lambda *_: None)


def test_align_tokens_finds_the_long_common_run():
    book_tokens = [f"w{i}" for i in range(500)]
    asr_tokens = ["junk", "junk", *book_tokens[10:490], "junk"]
    pairs, unaligned = align_tokens(book_tokens, asr_tokens)
    assert len(pairs) >= 470
    assert all(book_tokens[b] == asr_tokens[a] for b, a in pairs)
    assert pairs == sorted(pairs)
    assert all(region[1] > region[0] or region[3] > region[2] for region in unaligned)
