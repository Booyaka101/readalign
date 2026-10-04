"""Sentence boundary detection for prose, good enough to drive a read-along highlight.

Deliberately dependency-free and conservative: a wrong split costs one mistimed highlight,
so the rules favour leaving two sentences joined over cutting an abbreviation in half.
"""

from __future__ import annotations

import re

_ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "rev", "hon", "gen", "col", "capt",
    "lieut", "lt", "sgt", "maj", "messrs", "mme", "mlle", "esq", "vol", "chap", "fig",
    "pp", "ed", "vs", "etc", "viz", "cf", "ibid", "al", "inc", "ltd", "co", "dept", "univ",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
    "i.e", "e.g", "a.m", "p.m", "u.s", "u.k",
}
# "no" is deliberately absent: a sentence really can end in the word, and the next-word
# heuristic already keeps "No. 5" (a digit is not uppercase) from splitting.

_TERMINATOR = re.compile(r"[.!?…]")
# Typographic quotes and dashes, deliberately: real ebooks are full of them.
_CLOSERS = "\"')]}’”»›*"  # noqa: RUF001
_OPENERS = "\"'([{‘“«‹—–-"  # noqa: RUF001
_WORD_BEFORE = re.compile(r"([\w.]+)$", re.UNICODE)


def _is_abbreviation(text: str, position: int) -> bool:
    match = _WORD_BEFORE.search(text[:position])
    if not match:
        return False
    word = match[1].rstrip(".").casefold()
    if word in _ABBREVIATIONS:
        return True
    # A single letter followed by a period is an initial ("J. Smith"), not a sentence end.
    return len(word) == 1 and word.isalpha()


def split_sentences(text: str) -> list[tuple[int, int]]:
    """Return ``(start, end)`` character ranges of the sentences in ``text``.

    Ranges exclude the whitespace between sentences and cover the whole string otherwise,
    so callers can map a range straight back onto the markup it came from.
    """
    if not text.strip():
        return []
    ranges: list[tuple[int, int]] = []
    start = 0
    position = 0
    length = len(text)
    while position < length:
        char = text[position]
        if not _TERMINATOR.match(char):
            position += 1
            continue
        if char == "." and _is_abbreviation(text, position):
            position += 1
            continue
        end = position + 1
        while end < length and _TERMINATOR.match(text[end]):
            end += 1
        while end < length and text[end] in _CLOSERS:
            end += 1
        rest = text[end:]
        if rest and not rest[0].isspace():
            position = end
            continue
        following = rest.lstrip()
        if following and not (following[0].isupper() or following[0] in _OPENERS):
            position = end
            continue
        chunk = text[start:end]
        if chunk.strip():
            ranges.append((start + len(chunk) - len(chunk.lstrip()), end))
        start = end
        position = end
    tail = text[start:]
    if tail.strip():
        ranges.append((start + len(tail) - len(tail.lstrip()), start + len(tail.rstrip())))
    return ranges
