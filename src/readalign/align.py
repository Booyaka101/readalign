"""Match the ebook's words to the narration's words and time every sentence.

The book and the transcript are both reduced to normalised tokens, then aligned with a two-stage
scheme: unique shared n-grams pin the two streams together at thousands of anchor points, and
only the small regions between anchors go through a real diff. That is what keeps a 20-hour book
inside a few hundred megabytes; a single global dynamic-programming matrix over 200k x 200k
tokens is what makes the older tools in this space fall over.
"""

from __future__ import annotations

import bisect
import difflib
from collections import defaultdict
from dataclasses import dataclass, field

from .asr import Word
from .audio import AudioTrack
from .epub import Sentence
from .errors import AlignmentError
from .textnorm import tokenize_words

#: n-gram lengths tried when pinning two regions together, longest (most reliable) first.
ANCHOR_SIZES = (10, 7, 5, 4, 3)

#: Regions at or below this size go straight to the diff instead of being anchored further.
SMALL_REGION = 3000

#: Above this many cells a diff is refused; the region is reported as unaligned instead.
MAX_DIFF_CELLS = 40_000_000

#: Minimum share of a sentence's tokens that must match for it to count as confidently timed.
CONFIDENT = 0.5


@dataclass
class TimedSentence:
    sentence: Sentence
    start: float
    end: float
    confidence: float
    track_index: int
    interpolated: bool = False
    #: Seconds the start could be out by: the width of the gap an unmatched sentence was
    #: placed inside, or how far a start had to be pushed to keep the order. A sentence
    #: anchored on its own narrated words has a drift of zero.
    drift: float = 0.0
    clamped: bool = False


@dataclass
class AlignmentResult:
    timed: list[TimedSentence] = field(default_factory=list)
    skipped: list[Sentence] = field(default_factory=list)
    trimmed_head: int = 0
    trimmed_tail: int = 0
    unaligned_regions: list[dict] = field(default_factory=list)
    matched_tokens: int = 0
    book_tokens: int = 0
    asr_tokens: int = 0


def _unique_ngram_anchors(
    book: list[str], asr: list[str], region: tuple[int, int, int, int], size: int
) -> list[tuple[int, int]]:
    """Positions where one n-gram occurs exactly once on each side of the region."""
    book_start, book_end, asr_start, asr_end = region
    if book_end - book_start < size or asr_end - asr_start < size:
        return []
    book_index: dict[tuple[str, ...], list[int]] = defaultdict(list)
    for position in range(book_start, book_end - size + 1):
        book_index[tuple(book[position: position + size])].append(position)
    asr_index: dict[tuple[str, ...], list[int]] = defaultdict(list)
    for position in range(asr_start, asr_end - size + 1):
        asr_index[tuple(asr[position: position + size])].append(position)
    pairs = [
        (positions[0], asr_index[key][0])
        for key, positions in book_index.items()
        if len(positions) == 1 and len(asr_index.get(key, ())) == 1
    ]
    pairs.sort()
    return pairs


def _longest_increasing(pairs: list[tuple[int, int]], size: int) -> list[tuple[int, int]]:
    """Longest chain of anchors that is strictly increasing and non-overlapping on both sides."""
    tails: list[int] = []
    chain_index: list[int] = []
    parents: list[int] = [-1] * len(pairs)
    for position, (_, asr_position) in enumerate(pairs):
        slot = bisect.bisect_left(tails, asr_position)
        if slot == len(tails):
            tails.append(asr_position)
            chain_index.append(position)
        else:
            tails[slot] = asr_position
            chain_index[slot] = position
        parents[position] = chain_index[slot - 1] if slot else -1
    chain: list[tuple[int, int]] = []
    cursor = chain_index[-1] if chain_index else -1
    while cursor >= 0:
        chain.append(pairs[cursor])
        cursor = parents[cursor]
    chain.reverse()
    kept: list[tuple[int, int]] = []
    for book_position, asr_position in chain:
        if kept and (book_position < kept[-1][0] + size or asr_position < kept[-1][1] + size):
            continue
        kept.append((book_position, asr_position))
    return kept


def _diff_pairs(
    book: list[str], asr: list[str], region: tuple[int, int, int, int]
) -> list[tuple[int, int]]:
    book_start, book_end, asr_start, asr_end = region
    matcher = difflib.SequenceMatcher(
        None, book[book_start:book_end], asr[asr_start:asr_end], autojunk=False
    )
    pairs: list[tuple[int, int]] = []
    for book_offset, asr_offset, length in matcher.get_matching_blocks():
        pairs.extend(
            (book_start + book_offset + step, asr_start + asr_offset + step)
            for step in range(length)
        )
    return pairs


def align_tokens(
    book: list[str], asr: list[str]
) -> tuple[list[tuple[int, int]], list[tuple[int, int, int, int]]]:
    """Align two token streams, returning matched pairs and the regions left unaligned."""
    pairs: list[tuple[int, int]] = []
    unaligned: list[tuple[int, int, int, int]] = []
    stack = [(0, len(book), 0, len(asr))]
    while stack:
        region = stack.pop()
        book_start, book_end, asr_start, asr_end = region
        book_size, asr_size = book_end - book_start, asr_end - asr_start
        if book_size <= 0 or asr_size <= 0:
            continue
        if book_size <= SMALL_REGION and asr_size <= SMALL_REGION:
            pairs.extend(_diff_pairs(book, asr, region))
            continue
        chain: list[tuple[int, int]] = []
        used_size = 0
        for size in ANCHOR_SIZES:
            candidates = _unique_ngram_anchors(book, asr, region, size)
            chain = _longest_increasing(candidates, size)
            if chain:
                used_size = size
                break
        if not chain:
            if book_size * asr_size <= MAX_DIFF_CELLS:
                pairs.extend(_diff_pairs(book, asr, region))
            else:
                unaligned.append(region)
            continue
        cursor_book, cursor_asr = book_start, asr_start
        for anchor_book, anchor_asr in chain:
            stack.append((cursor_book, anchor_book, cursor_asr, anchor_asr))
            pairs.extend(
                (anchor_book + step, anchor_asr + step) for step in range(used_size)
            )
            cursor_book, cursor_asr = anchor_book + used_size, anchor_asr + used_size
        stack.append((cursor_book, book_end, cursor_asr, asr_end))
    pairs.sort()
    return pairs, unaligned


def _track_for(tracks: list[AudioTrack], moment: float) -> AudioTrack:
    for track in tracks:
        if moment < track.end:
            return track
    return tracks[-1]


def align(
    sentences: list[Sentence],
    words: list[Word],
    tracks: list[AudioTrack],
    *,
    log=print,
) -> AlignmentResult:
    """Time every sentence in the book against the narration."""
    if not sentences:
        raise AlignmentError("the ebook contains no sentences to align")
    if not words:
        raise AlignmentError(
            "the narration produced no words. Check that the audio actually contains speech "
            "and that --language matches it."
        )

    book_tokens: list[str] = []
    sentence_spans: list[tuple[int, int]] = []
    for sentence in sentences:
        tokens, _ = tokenize_words(sentence.words)
        sentence_spans.append((len(book_tokens), len(book_tokens) + len(tokens)))
        book_tokens.extend(tokens)

    asr_tokens, asr_origin = tokenize_words([word.text for word in words])
    if not book_tokens:
        raise AlignmentError("the ebook contains no words to align")

    log(f"  aligning {len(book_tokens)} book tokens against {len(asr_tokens)} spoken tokens")
    pairs, unaligned = align_tokens(book_tokens, asr_tokens)
    if not pairs:
        raise AlignmentError(
            "no part of the narration matched the ebook. Are the audiobook and the ebook the "
            "same work, and is --language correct?"
        )

    mapping = [-1] * len(book_tokens)
    for book_position, asr_position in pairs:
        mapping[book_position] = asr_position

    first_match = pairs[0][0]
    last_match = pairs[-1][0]

    result = AlignmentResult(
        matched_tokens=len(pairs),
        book_tokens=len(book_tokens),
        asr_tokens=len(asr_tokens),
        unaligned_regions=[
            {
                "book_tokens": [region[0], region[1]],
                "audio_seconds": [
                    round(words[asr_origin[min(region[2], len(asr_origin) - 1)]].start, 2),
                    round(words[asr_origin[min(region[3] - 1, len(asr_origin) - 1)]].end, 2),
                ] if asr_origin else [0.0, 0.0],
            }
            for region in unaligned
        ],
    )

    timed: list[TimedSentence | None] = [None] * len(sentences)
    for index, sentence in enumerate(sentences):
        start_token, end_token = sentence_spans[index]
        if end_token <= start_token or end_token <= first_match or start_token > last_match:
            continue
        matched = [
            mapping[position]
            for position in range(start_token, end_token)
            if mapping[position] >= 0
        ]
        if not matched:
            continue
        first_word = words[asr_origin[matched[0]]]
        last_word = words[asr_origin[matched[-1]]]
        confidence = len(matched) / (end_token - start_token)
        track = _track_for(tracks, first_word.start)
        timed[index] = TimedSentence(
            sentence=sentence,
            start=first_word.start,
            end=max(last_word.end, first_word.start + 0.05),
            confidence=round(min(1.0, confidence), 4),
            track_index=track.index,
        )

    _interpolate_gaps(sentences, sentence_spans, timed, tracks, first_match, last_match)
    _enforce_monotonic(timed, tracks)

    result.timed = [entry for entry in timed if entry is not None]
    result.skipped = [
        sentence for index, sentence in enumerate(sentences) if timed[index] is None
    ]
    result.trimmed_head = sum(
        1 for index, _ in enumerate(sentences) if sentence_spans[index][1] <= first_match
    )
    result.trimmed_tail = sum(
        1 for index, _ in enumerate(sentences) if sentence_spans[index][0] > last_match
    )
    return result


def _interpolate_gaps(
    sentences: list[Sentence],
    spans: list[tuple[int, int]],
    timed: list[TimedSentence | None],
    tracks: list[AudioTrack],
    first_match: int,
    last_match: int,
) -> None:
    """Give unmatched sentences inside the narrated body a time from their neighbours."""
    anchored = [index for index, entry in enumerate(timed) if entry is not None]
    if not anchored:
        return
    for index, sentence in enumerate(sentences):
        if timed[index] is not None:
            continue
        start_token, end_token = spans[index]
        if end_token <= start_token or end_token <= first_match or start_token > last_match:
            continue
        position = bisect.bisect_left(anchored, index)
        before = anchored[position - 1] if position else None
        after = anchored[position] if position < len(anchored) else None
        if before is None or after is None:
            continue
        previous, following = timed[before], timed[after]
        span_start, span_end = spans[before][1], spans[after][0]
        total = max(1, span_end - span_start)
        window = max(0.0, following.start - previous.end)
        start = previous.end + window * (start_token - span_start) / total
        end = previous.end + window * (end_token - span_start) / total
        track = _track_for(tracks, start)
        timed[index] = TimedSentence(
            sentence=sentence,
            start=start,
            end=max(end, start + 0.05),
            confidence=0.0,
            track_index=track.index,
            interpolated=True,
            drift=round(window, 3),
        )


def _enforce_monotonic(timed: list[TimedSentence | None], tracks: list[AudioTrack]) -> None:
    """Keep clips ordered, inside their own file, and never past the file's real duration.

    How far a start has to be pushed is recorded as drift: a sentence matched to the wrong
    occurrence of its own words is what makes the order go backwards in the first place.
    """
    by_track = {track.index: track for track in tracks}
    previous_end = 0.0
    for entry in timed:
        if entry is None:
            continue
        track = by_track[entry.track_index]
        anchored_start = entry.start
        entry.start = max(entry.start, previous_end)
        entry.drift = round(max(entry.drift, entry.start - anchored_start), 3)
        if entry.start >= track.end:
            entry.start = max(track.offset, track.end - 0.05)
        entry.end = max(entry.end, entry.start + 0.05)
        if entry.end > track.end:
            entry.end = track.end
            entry.clamped = True
        if entry.end <= entry.start:
            entry.start = max(track.offset, entry.end - 0.05)
        previous_end = entry.end
