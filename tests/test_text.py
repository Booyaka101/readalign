"""Normalisation and sentence splitting, the two places a wrong answer silently ruins timings."""

import pytest

from readalign.clock import format_clock, parse_clock
from readalign.sentences import split_sentences
from readalign.textnorm import tokenize_word, tokenize_words


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1797", ["seventeen", "ninety", "seven"]),
        ("1800", ["eighteen", "hundred"]),
        ("1,000", ["one", "thousand"]),
        ("17th", ["seventeenth"]),
        ("To-morrow", ["tomorrow"]),
        ("don't", ["dont"]),
        ("café", ["cafe"]),
        ("IV", ["four"]),
        ("I", ["i"]),
        ("—", []),
    ],
)
def test_tokenize_word(raw, expected):
    assert tokenize_word(raw) == expected


def test_tokenize_words_tracks_origins():
    tokens, origins = tokenize_words(["In", "1797", "he", "left"])
    assert tokens == ["in", "seventeen", "ninety", "seven", "he", "left"]
    assert origins == [0, 1, 1, 1, 2, 3]


def test_sentence_splitting_handles_abbreviations_and_quotes():
    text = 'Mr. Darcy went home. He said "Hello there!" Then he left... Did he? Yes.'
    pieces = [text[start:end] for start, end in split_sentences(text)]
    assert pieces == [
        "Mr. Darcy went home.",
        'He said "Hello there!"',
        "Then he left...",
        "Did he?",
        "Yes.",
    ]


def test_sentence_splitting_keeps_single_initials_together():
    text = "It was written by J. R. Hartley. He was pleased."
    pieces = [text[start:end] for start, end in split_sentences(text)]
    assert pieces == ["It was written by J. R. Hartley.", "He was pleased."]


def test_clock_round_trips():
    assert format_clock(3661.5) == "1:01:01.500"
    assert format_clock(59.9996) == "0:01:00.000"
    assert parse_clock("1:01:01.500") == pytest.approx(3661.5)
    assert parse_clock("90.5s") == pytest.approx(90.5)
    assert parse_clock("2:30") == pytest.approx(150.0)
    assert parse_clock("1.5min") == pytest.approx(90.0)


def test_clock_rejects_nonsense():
    with pytest.raises(ValueError):
        parse_clock("soon")
