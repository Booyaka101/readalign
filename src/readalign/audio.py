"""Enumerate the narration, measure it with ffprobe, and prepare what actually ships.

Original files are never modified. Everything that goes into the EPUB is either a stream copy or
a transcode written into the work directory, and every timestamp readalign produces is measured
against those prepared files, so the clock values always describe the audio the reader plays.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import wave
from dataclasses import dataclass, field

import numpy as np

from .errors import DependencyError, DRMError, InputError

AUDIO_EXTENSIONS = {
    ".mp3", ".m4a", ".m4b", ".mp4", ".flac", ".ogg", ".oga", ".opus", ".wav", ".aiff", ".aif",
    ".wma", ".aac",
}
DRM_EXTENSIONS = {".aax", ".aa", ".acsm", ".aaxc"}

#: Codec tags Apple FairPlay uses; ffprobe reports these for a protected track.
_DRM_CODEC_TAGS = {"drmi", "drms", "enca", "encv"}

SAMPLE_RATE = 16000

_SILENCE_RE = re.compile(r"silence_(start|end): (-?[\d.]+)")
_NUMBER_RE = re.compile(r"(\d+)")


@dataclass
class Chapter:
    start: float
    end: float
    title: str


@dataclass
class AudioTrack:
    """One prepared audio file, plus where it sits in the book's overall timeline."""

    index: int
    source: str
    path: str
    href: str
    media_type: str
    duration: float
    offset: float
    chapters: list[Chapter] = field(default_factory=list)
    transcoded: bool = False
    wav_path: str | None = None

    @property
    def end(self) -> float:
        return self.offset + self.duration


def natural_key(name: str) -> tuple:
    """Sort ``track2`` before ``track10``, the way a person names audiobook parts."""
    parts = _NUMBER_RE.split(os.path.basename(name).casefold())
    return tuple(int(part) if part.isdigit() else part for part in parts)


def require_ffmpeg() -> None:
    missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
    if missing:
        raise DependencyError(
            f"{' and '.join(missing)} not found on PATH. readalign needs ffmpeg installed: "
            "https://ffmpeg.org/download.html"
        )


def _run(command: list[str], what: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", check=False)
    except OSError as exc:
        raise DependencyError(f"could not run {command[0]} ({what}): {exc}") from exc


def ffprobe(path: str) -> dict:
    """Probe a media file, returning the parsed ffprobe JSON."""
    result = _run(
        [
            "ffprobe", "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", "-show_chapters", path,
        ],
        f"probing {path}",
    )
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        message = detail[-1] if detail else f"exit code {result.returncode}"
        raise InputError(f"ffprobe could not read {path}: {message}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise InputError(f"ffprobe returned unreadable output for {path}: {exc}") from exc


def _audio_stream(probe: dict, path: str) -> dict:
    streams = [s for s in probe.get("streams", []) if s.get("codec_type") == "audio"]
    if not streams:
        raise InputError(f"{path} contains no audio stream")
    return streams[0]


def _duration_of(probe: dict, path: str) -> float:
    for source in (probe.get("format", {}), _audio_stream(probe, path)):
        value = source.get("duration")
        if value:
            try:
                duration = float(value)
            except ValueError:
                continue
            if duration > 0:
                return duration
    raise InputError(f"{path} has no readable duration; is it a complete audio file?")


def _reject_drm(path: str, probe: dict | None = None) -> None:
    name = os.path.basename(path)
    if os.path.splitext(path)[1].lower() in DRM_EXTENSIONS:
        raise DRMError(f"readalign does not handle DRM-protected files: {name}")
    if probe is None:
        return
    for stream in probe.get("streams", []):
        if (stream.get("codec_tag_string") or "").lower() in _DRM_CODEC_TAGS:
            raise DRMError(f"readalign does not handle DRM-protected files: {name}")
    raw_tags = probe.get("format", {}).get("tags") or {}
    tags = {key.lower(): value for key, value in raw_tags.items()}
    if any("drm" in key for key in tags):
        raise DRMError(f"readalign does not handle DRM-protected files: {name}")


def discover_inputs(path: str) -> list[str]:
    """Return the audio files to use, in playback order."""
    if not os.path.exists(path):
        raise InputError(f"audio not found: {path}")
    if os.path.isfile(path):
        _reject_drm(path)
        if os.path.splitext(path)[1].lower() not in AUDIO_EXTENSIONS:
            raise InputError(
                f"{path} does not look like an audio file "
                f"(expected one of {', '.join(sorted(AUDIO_EXTENSIONS))})"
            )
        return [path]
    entries = []
    for name in os.listdir(path):
        suffix = os.path.splitext(name)[1].lower()
        if suffix in DRM_EXTENSIONS:
            # Refuse the whole directory rather than transcribe around a protected file.
            raise DRMError(f"readalign does not handle DRM-protected files: {name}")
        if suffix in AUDIO_EXTENSIONS and os.path.isfile(os.path.join(path, name)):
            entries.append(os.path.join(path, name))
    if not entries:
        wanted = ", ".join(sorted(AUDIO_EXTENSIONS))
        raise InputError(f"no audio files in {path} (looked for {wanted})")
    return sorted(entries, key=natural_key)


def _chapters_from(probe: dict) -> list[Chapter]:
    chapters: list[Chapter] = []
    for entry in probe.get("chapters", []):
        try:
            start = float(entry.get("start_time", 0.0))
            end = float(entry.get("end_time", 0.0))
        except (TypeError, ValueError):
            continue
        if end <= start:
            continue
        title = (entry.get("tags") or {}).get("title") or f"Chapter {len(chapters) + 1}"
        chapters.append(Chapter(start=start, end=end, title=title))
    return chapters


def _output_plan(path: str, probe: dict) -> tuple[str, str, bool]:
    """Pick the extension, media type and whether a transcode is needed for a source file."""
    stream = _audio_stream(probe, path)
    codec = (stream.get("codec_name") or "").lower()
    names = (probe.get("format", {}).get("format_name") or "").split(",")
    container = {name.strip() for name in names}
    if codec == "mp3":
        return ".mp3", "audio/mpeg", False
    if codec == "aac" and container & {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"}:
        return ".m4a", "audio/mp4", False
    if codec == "opus" and container & {"ogg"}:
        return ".opus", "audio/ogg; codecs=opus", False
    return ".m4a", "audio/mp4", True


def prepare_tracks(sources: list[str], workdir: str, bitrate: str, log=print) -> list[AudioTrack]:
    """Copy or transcode every source into an EPUB core media type and measure the result.

    MP3, AAC-in-MP4 and Opus-in-Ogg are core media types in EPUB 3, so they are stream-copied.
    Anything else (FLAC, WAV, ALAC, WMA) is transcoded to AAC, because a reading system is not
    required to play it.
    """
    audio_dir = os.path.join(workdir, "audio")
    os.makedirs(audio_dir, exist_ok=True)
    tracks: list[AudioTrack] = []
    offset = 0.0
    for index, source in enumerate(sources, start=1):
        probe = ffprobe(source)
        _reject_drm(source, probe)
        suffix, media_type, transcode = _output_plan(source, probe)
        target = os.path.join(audio_dir, f"part{index:03d}{suffix}")
        label = f"[{index}/{len(sources)}] {os.path.basename(source)}"
        log(f"  {label} -> {'transcoding to AAC' if transcode else 'stream copy'}")
        command = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", source,
                   "-map", "0:a:0", "-map_chapters", "0", "-vn"]
        command += ["-c:a", "aac", "-b:a", bitrate] if transcode else ["-c:a", "copy"]
        if suffix == ".m4a":
            command += ["-movflags", "+faststart"]
        result = _run([*command, target], f"preparing {source}")
        if result.returncode != 0 or not os.path.exists(target):
            detail = (result.stderr or "").strip().splitlines()
            raise InputError(
                f"ffmpeg could not prepare {source}: {detail[-1] if detail else 'unknown error'}"
            )
        prepared = ffprobe(target)
        duration = _duration_of(prepared, target)
        tracks.append(
            AudioTrack(
                index=index,
                source=source,
                path=target,
                href=f"audio/part{index:03d}{suffix}",
                media_type=media_type,
                duration=duration,
                offset=offset,
                chapters=_chapters_from(prepared) or _chapters_from(probe),
                transcoded=transcode,
            )
        )
        offset += duration
    return tracks


def decode_wav(track: AudioTrack, workdir: str) -> str:
    """Decode a prepared track to 16 kHz mono PCM for the recogniser."""
    wav_dir = os.path.join(workdir, "wav")
    os.makedirs(wav_dir, exist_ok=True)
    target = os.path.join(wav_dir, f"part{track.index:03d}.wav")
    result = _run(
        [
            "ffmpeg", "-nostdin", "-v", "error", "-y", "-i", track.path,
            "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", "-f", "wav", target,
        ],
        f"decoding {track.path}",
    )
    if result.returncode != 0 or not os.path.exists(target):
        detail = (result.stderr or "").strip().splitlines()
        raise InputError(
            f"ffmpeg could not decode {track.source}: {detail[-1] if detail else 'unknown error'}"
        )
    track.wav_path = target
    return target


def read_wav_segment(wav_path: str, start: float, end: float) -> np.ndarray:
    """Read ``[start, end)`` seconds of a 16 kHz mono WAV as float32 in -1..1."""
    with wave.open(wav_path, "rb") as handle:
        frames = handle.getnframes()
        first = max(0, min(frames, int(start * SAMPLE_RATE)))
        last = max(first, min(frames, int(end * SAMPLE_RATE)))
        handle.setpos(first)
        raw = handle.readframes(last - first)
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


def detect_silences(
    wav_path: str, noise_db: int = -32, min_duration: float = 0.35
) -> list[tuple[float, float]]:
    """Find silent stretches, used to cut long files where no word is being spoken."""
    result = _run(
        [
            "ffmpeg", "-nostdin", "-v", "info", "-i", wav_path,
            "-af", f"silencedetect=noise={noise_db}dB:d={min_duration}", "-f", "null", "-",
        ],
        f"scanning {wav_path} for silence",
    )
    silences: list[tuple[float, float]] = []
    start: float | None = None
    for kind, value in _SILENCE_RE.findall(result.stderr or ""):
        moment = float(value)
        if kind == "start":
            start = moment
        elif start is not None:
            silences.append((start, moment))
            start = None
    return silences


def plan_windows(track: AudioTrack, max_seconds: float) -> list[tuple[float, float]]:
    """Split a track into recogniser-sized windows that never cut through speech.

    Chapter marks are used when the file has them; otherwise the cut lands in the middle of the
    longest silence near the target boundary. This keeps peak memory flat for a 20-hour m4b.
    """
    duration = track.duration
    if duration <= max_seconds:
        return [(0.0, duration)]

    boundaries: list[float] = []
    if track.chapters:
        for chapter in track.chapters:
            if 0.0 < chapter.start < duration:
                boundaries.append(chapter.start)
    if not _covers(boundaries, duration, max_seconds):
        silences = detect_silences(track.wav_path or track.path)
        boundaries = _merge_boundaries(boundaries, silences, duration, max_seconds)

    windows: list[tuple[float, float]] = []
    cursor = 0.0
    for boundary in [*sorted(boundaries), duration]:
        if boundary - cursor <= 0.01:
            continue
        windows.append((cursor, boundary))
        cursor = boundary
    return windows or [(0.0, duration)]


def _covers(boundaries: list[float], duration: float, max_seconds: float) -> bool:
    cursor = 0.0
    for boundary in [*sorted(boundaries), duration]:
        if boundary - cursor > max_seconds:
            return False
        cursor = boundary
    return True


def _merge_boundaries(
    boundaries: list[float],
    silences: list[tuple[float, float]],
    duration: float,
    max_seconds: float,
) -> list[float]:
    chosen = sorted(boundaries)
    cursor = 0.0
    result: list[float] = []
    pending = [*chosen, duration]
    for boundary in pending:
        while boundary - cursor > max_seconds:
            target = cursor + max_seconds
            candidates = [
                (end - start, (start + end) / 2)
                for start, end in silences
                if cursor + max_seconds * 0.5 < (start + end) / 2 < min(target + 120.0, boundary)
            ]
            cut = max(candidates)[1] if candidates else target
            result.append(cut)
            cursor = cut
        if boundary < duration:
            result.append(boundary)
        cursor = boundary
    return result
