"""Write one media overlay document per content document.

Structure follows EPUB 3.3 section 9.2.2: a ``smil`` root at version 3.0, a ``body`` carrying
``par`` elements, and nested ``seq`` wrappers mirroring the structures a reading system may let
the user skip (a footnote, a page number) or escape out of (a table, a list, a figure).
"""

from __future__ import annotations

import math
import posixpath
from dataclasses import replace
from urllib.parse import quote

from lxml import etree

from .align import TimedSentence
from .audio import AudioTrack
from .clock import format_clock
from .errors import InputError

SMIL_NS = "http://www.w3.org/ns/SMIL"
EPUB_NS = "http://www.idpf.org/2007/ops"

_NSMAP = {None: SMIL_NS, "epub": EPUB_NS}


def relative_href(from_path: str, to_path: str) -> str:
    """Path from one archive member to another, as a path-relative-scheme-less-URL string."""
    relative = posixpath.relpath(to_path, posixpath.dirname(from_path))
    return quote(relative.replace("\\", "/"))


def continuous_clips(
    entries: list[TimedSentence], tracks: list[AudioTrack]
) -> list[TimedSentence]:
    """Cover inter-sentence audio without changing the alignment or excerpt boundaries."""
    positions = {track.index: index for index, track in enumerate(tracks)}
    for index, track in enumerate(tracks):
        if (not math.isfinite(track.offset) or not math.isfinite(track.duration)
                or track.offset < 0 or track.duration <= 0
                or (index and not math.isclose(track.offset, tracks[index - 1].end,
                                               rel_tol=0, abs_tol=1e-6))):
            raise InputError("audio tracks must form a finite, contiguous playback timeline")
    if len(positions) != len(tracks):
        raise InputError("audio track indices must be unique")
    previous_start = -math.inf
    for entry in entries:
        if entry.track_index not in positions:
            raise InputError(f"alignment references unknown audio track {entry.track_index}")
        track = tracks[positions[entry.track_index]]
        if (not math.isfinite(entry.start) or not math.isfinite(entry.end)
                or not track.offset <= entry.start < entry.end <= track.end
                or entry.start < previous_start):
            raise InputError("alignment timestamps must be finite, ordered and inside their track")
        previous_start = entry.start
    clips = []
    for index, entry in enumerate(entries):
        end = entries[index + 1].start if index + 1 < len(entries) else entry.end
        start = entry.start
        position = positions[entry.track_index]
        # A gap can span files, including a file with no matched words. Each SMIL
        # clip must still use that file's local clock and stop at its real end.
        while position < len(tracks) and start < end:
            track = tracks[position]
            stop = min(end, track.end)
            clip = replace(entry, start=start, end=stop, track_index=track.index)
            begin_ms, end_ms = _clip_milliseconds(clip, track)
            if end_ms > begin_ms:
                clips.append(clip)
            start = stop
            position += 1
    if entries and not clips:
        raise InputError("the aligned audio range is too short for millisecond SMIL clips")
    return clips


def _clip_milliseconds(entry: TimedSentence, track: AudioTrack) -> tuple[int, int]:
    """Use the same file-local rounding for clip serialization and metadata."""
    return round((entry.start - track.offset) * 1000), round((entry.end - track.offset) * 1000)


def build_smil(
    smil_path: str,
    doc_path: str,
    entries: list[TimedSentence],
    tracks: dict[int, AudioTrack],
    audio_paths: dict[int, str],
) -> bytes:
    """Serialise the overlay for one content document."""
    text_href = relative_href(smil_path, doc_path)
    root = etree.Element(f"{{{SMIL_NS}}}smil", nsmap=_NSMAP)
    root.set("version", "3.0")
    body = etree.SubElement(root, f"{{{SMIL_NS}}}body")
    body.set(f"{{{EPUB_NS}}}textref", text_href)

    # Open seqs, innermost last. Nesting has to mirror the document for escapability to work,
    # and entries arrive in document order, so a structure is left as soon as its ids stop matching.
    open_seqs: list[tuple[str, etree._Element]] = []
    for entry in entries:
        track = tracks[entry.track_index]
        begin_ms, end_ms = _clip_milliseconds(entry, track)
        if end_ms <= begin_ms:
            continue
        chain = entry.sentence.structure
        shared = 0
        while (
            shared < len(open_seqs)
            and shared < len(chain)
            and open_seqs[shared][0] == chain[shared].container_id
        ):
            shared += 1
        del open_seqs[shared:]
        for level in chain[shared:]:
            parent = open_seqs[-1][1] if open_seqs else body
            seq = etree.SubElement(parent, f"{{{SMIL_NS}}}seq")
            seq.set(f"{{{EPUB_NS}}}textref", f"{text_href}#{quote(level.container_id, safe='')}")
            seq.set(f"{{{EPUB_NS}}}type", level.epub_type)
            open_seqs.append((level.container_id, seq))
        container = open_seqs[-1][1] if open_seqs else body
        par = etree.SubElement(container, f"{{{SMIL_NS}}}par")
        text = etree.SubElement(par, f"{{{SMIL_NS}}}text")
        text.set("src", f"{text_href}#{quote(entry.sentence.fragment_id, safe='')}")
        audio = etree.SubElement(par, f"{{{SMIL_NS}}}audio")
        audio.set("src", relative_href(smil_path, audio_paths[entry.track_index]))
        audio.set("clipBegin", format_clock(begin_ms / 1000))
        audio.set("clipEnd", format_clock(end_ms / 1000))
    return etree.tostring(root, xml_declaration=True, encoding="utf-8", pretty_print=True)


def overlay_duration(entries: list[TimedSentence], tracks: dict[int, AudioTrack]) -> float:
    """Playing time of an overlay: the sum of its clips, which is what media:duration means."""
    return sum(max(0, end - begin) for begin, end in (
        _clip_milliseconds(entry, tracks[entry.track_index]) for entry in entries
    )) / 1000
