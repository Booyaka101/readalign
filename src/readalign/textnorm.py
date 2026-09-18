"""Turn raw words from an ebook or an ASR transcript into comparable tokens.

Both sides go through :func:`tokenize_word`, so the aligner only ever compares like with like:
case folded, accent stripped, punctuation removed, numbers and roman numerals spelled out.
"""

from __future__ import annotations

import re
import unicodedata

_UNITS = [
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
    "seventeen", "eighteen", "nineteen",
]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_SCALES = [(1_000_000_000, "billion"), (1_000_000, "million"), (1_000, "thousand")]

_ORDINALS = {
    "one": "first",
    "two": "second",
    "three": "third",
    "five": "fifth",
    "eight": "eighth",
    "nine": "ninth",
    "twelve": "twelfth",
}

_ROMAN_VALUES = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
_ROMAN_RE = re.compile(r"^M{0,4}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$")
_ORDINAL_SUFFIX_RE = re.compile(r"^(\d+)(?:st|nd|rd|th)$")
_DIGITS_RE = re.compile(r"\d")


def _small_number_words(value: int) -> list[str]:
    if value < 20:
        return [_UNITS[value]]
    if value < 100:
        tens, unit = divmod(value, 10)
        words = [_TENS[tens]]
        if unit:
            words.append(_UNITS[unit])
        return words
    hundreds, rest = divmod(value, 100)
    words = [_UNITS[hundreds], "hundred"]
    if rest:
        words.extend(_small_number_words(rest))
    return words


def number_words(value: int) -> list[str]:
    """Spell an integer in English, e.g. 1207 -> ['one', 'thousand', 'two', 'hundred', 'seven']."""
    if value < 0:
        return ["minus", *number_words(-value)]
    if value == 0:
        return ["zero"]
    words: list[str] = []
    remainder = value
    for scale, name in _SCALES:
        count, remainder = divmod(remainder, scale)
        if count:
            words.extend([*number_words(count), name])
    if remainder:
        words.extend(_small_number_words(remainder))
    return words


def _year_words(value: int) -> list[str]:
    """Read a four-digit year the way a narrator does: 1797 -> seventeen ninety seven."""
    high, low = divmod(value, 100)
    if low == 0:
        return [*_small_number_words(high), "hundred"]
    if low < 10:
        return [*_small_number_words(high), "oh", _UNITS[low]]
    return [*_small_number_words(high), *_small_number_words(low)]


def _ordinal_words(value: int) -> list[str]:
    words = number_words(value)
    last = words[-1]
    if last in _ORDINALS:
        words[-1] = _ORDINALS[last]
    elif last.endswith("y"):
        words[-1] = last[:-1] + "ieth"
    else:
        words[-1] = last + "th"
    return words


def roman_value(token: str) -> int | None:
    """Value of a canonical roman numeral, or None if the token is not one."""
    lowered = token.lower()
    if len(lowered) < 2 or not _ROMAN_RE.match(token.upper()):
        return None
    total = 0
    previous = 0
    for char in reversed(lowered):
        value = _ROMAN_VALUES[char]
        total += value if value >= previous else -value
        previous = max(previous, value)
    return total


def _strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def tokenize_word(raw: str) -> list[str]:
    """Normalise one whitespace-delimited word into zero or more comparison tokens."""
    if not raw:
        return []
    # Roman numerals are only recognised as written in the source, before case folding,
    # so that the pronoun "I" and ordinary lowercase words are never rewritten as numbers.
    stripped = re.sub(r"[^\w]", "", _strip_accents(raw), flags=re.UNICODE)
    if not stripped:
        return []
    if stripped.isupper():
        value = roman_value(stripped)
        if value is not None:
            return number_words(value)
    folded = stripped.casefold()
    if _DIGITS_RE.search(folded):
        return _number_tokens(folded)
    return [folded] if folded else []


def _number_tokens(folded: str) -> list[str]:
    ordinal = _ORDINAL_SUFFIX_RE.match(folded)
    if ordinal:
        return _ordinal_words(int(ordinal[1]))
    if folded.isdigit():
        value = int(folded)
        if len(folded) == 4 and 1100 <= value <= 2099:
            return _year_words(value)
        return number_words(value)
    # Mixed letters and digits (a chapter label like "12a", a model number): split the runs.
    tokens: list[str] = []
    for part in re.findall(r"\d+|[^\d]+", folded):
        tokens.extend(number_words(int(part)) if part.isdigit() else [part])
    return tokens


def tokenize_words(raw_words: list[str]) -> tuple[list[str], list[int]]:
    """Tokenise a word list, returning the tokens and the source index each token came from."""
    tokens: list[str] = []
    origins: list[int] = []
    for index, raw in enumerate(raw_words):
        for token in tokenize_word(raw):
            tokens.append(token)
            origins.append(index)
    return tokens, origins


def split_raw_words(text: str) -> list[str]:
    """Split running text into raw words on whitespace."""
    return text.split()
