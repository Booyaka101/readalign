"""Write the sidecars: WebVTT cues for players, and one flat align.json for tooling.

The EPUB is self-contained, but the same timings are useful outside it. WebVTT is per audio
file, because that is what a player loads next to a track, so a chapter spanning two files
produces two cue files.
"""

from __future__ import annotations

import contextlib
import json
import os
import posixpath
from collections import defaultdict

from .align import AlignmentResult, TimedSentence
from .audio import AudioTrack
from .fs import ensure_directory, write_text

SIDECAR_VERSION = 1


def vtt_timestamp(seconds: float) -> str:
    """WebVTT needs HH:MM:SS.mmm, and unlike SMIL it wants the hours field zero-padded."""
    milliseconds = max(0, round(seconds * 1000))
    hours, rest = divmod(milliseconds, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def _cue_text(text: str) -> str:
    collapsed = " ".join(text.split())
    return collapsed.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_vtt(entries: list[TimedSentence], track: AudioTrack, audio_name: str) -> str:
    """One WebVTT file: cues in the audio file's own timeline, not the book's."""
    lines = ["WEBVTT", "", f"NOTE audio {audio_name}", ""]
    for entry in entries:
        start = max(0.0, entry.start - track.offset)
        end = max(start + 0.001, min(entry.end - track.offset, track.duration))
        lines.append(entry.sentence.fragment_id)
        lines.append(f"{vtt_timestamp(start)} --> {vtt_timestamp(end)}")
        lines.append(_cue_text(entry.sentence.text))
        lines.append("")
    return "\n".join(lines)


def build_align_json(
    result: AlignmentResult,
    tracks: dict[int, AudioTrack],
    doc_hrefs: dict[int, str],
    audio_hrefs: dict[int, str],
    *,
    title: str | None,
) -> str:
    """The whole alignment as one flat JSON document, in absolute book time."""
    rows = []
    for entry in result.timed:
        track = tracks[entry.track_index]
        rows.append(
            {
                "id": entry.sentence.fragment_id,
                "document": doc_hrefs.get(entry.sentence.doc_index, ""),
                "audio": audio_hrefs.get(entry.track_index, posixpath.basename(track.href)),
                "start": round(entry.start, 3),
                "end": round(entry.end, 3),
                "clip_begin": round(max(0.0, entry.start - track.offset), 3),
                "clip_end": round(max(0.0, entry.end - track.offset), 3),
                "confidence": entry.confidence,
                "interpolated": entry.interpolated,
                "drift": round(entry.drift, 3),
                "text": " ".join(entry.sentence.text.split()),
            }
        )
    payload = {
        "version": SIDECAR_VERSION,
        "title": title,
        "sentences": len(rows),
        "audio": [
            {
                "index": index,
                "href": audio_hrefs.get(index, posixpath.basename(track.href)),
                "source": os.path.basename(track.source),
                "offset": round(track.offset, 3),
                "duration": round(track.duration, 3),
            }
            for index, track in sorted(tracks.items())
        ],
        "alignment": rows,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _clear_previous(directory: str) -> None:
    """Drop the sidecars of an earlier run: a re-run can name its files differently."""
    for name in os.listdir(directory):
        if name == "align.json" or name.endswith(".vtt"):
            with contextlib.suppress(OSError):
                os.unlink(os.path.join(directory, name))


def write_sidecars(
    directory: str,
    result: AlignmentResult,
    tracks: list[AudioTrack],
    doc_hrefs: dict[int, str],
    audio_hrefs: dict[int, str],
    *,
    title: str | None = None,
) -> list[str]:
    """Write align.json plus one WebVTT per (document, audio file) pair. Returns the paths."""
    by_index = {track.index: track for track in tracks}
    ensure_directory(directory, "sidecar directory")
    _clear_previous(directory)

    grouped: dict[tuple[int, int], list[TimedSentence]] = defaultdict(list)
    for entry in result.timed:
        grouped[(entry.sentence.doc_index, entry.track_index)].append(entry)

    written: list[str] = []
    multi_track = {
        doc_index
        for doc_index in {key[0] for key in grouped}
        if len({key[1] for key in grouped if key[0] == doc_index}) > 1
    }
    for (doc_index, track_index), entries in sorted(grouped.items()):
        track = by_index[track_index]
        href = doc_hrefs.get(doc_index, f"doc{doc_index}")
        stem = posixpath.splitext(posixpath.basename(href))[0]
        name = f"{stem}.part{track_index:03d}.vtt" if doc_index in multi_track else f"{stem}.vtt"
        path = os.path.join(directory, name)
        audio_name = audio_hrefs.get(track_index, posixpath.basename(track.href))
        write_text(path, build_vtt(entries, track, audio_name))
        written.append(path)

    path = os.path.join(directory, "align.json")
    write_text(path, build_align_json(result, by_index, doc_hrefs, audio_hrefs, title=title))
    written.append(path)
    return written

