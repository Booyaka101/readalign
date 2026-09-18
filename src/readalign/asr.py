"""Transcribe the narration to words with timestamps, in absolute book time.

The result is a flat, ordered list of words spanning every track, which is what the aligner
wants: it never has to know which file a word came from.
"""

from __future__ import annotations

import contextlib
import glob
import hashlib
import json
import os
import site
import sys
import time
from dataclasses import asdict, dataclass

from .audio import AudioTrack, read_wav_segment
from .errors import DependencyError

_CUDA_READY = False


def enable_cuda_libraries() -> None:
    """Put the pip-installed NVIDIA runtime DLLs where CTranslate2 can find them.

    CTranslate2 loads cuBLAS with a plain ``LoadLibrary`` that consults ``PATH``, so on Windows
    ``os.add_dll_directory`` alone is not enough: without this, faster-whisper silently falls back
    to the CPU while ``get_cuda_device_count()`` still reports a device.
    """
    global _CUDA_READY
    if _CUDA_READY:
        return
    _CUDA_READY = True
    roots = [*site.getsitepackages(), site.getusersitepackages()]
    directories = []
    for root in roots:
        directories.extend(glob.glob(os.path.join(root, "nvidia", "*", "bin")))
        directories.extend(glob.glob(os.path.join(root, "nvidia", "*", "lib")))
    for directory in directories:
        if os.path.isdir(directory):
            if hasattr(os, "add_dll_directory"):
                with contextlib.suppress(OSError):
                    os.add_dll_directory(directory)
            if directory not in os.environ.get("PATH", "").split(os.pathsep):
                os.environ["PATH"] = directory + os.pathsep + os.environ.get("PATH", "")


@dataclass
class Word:
    """One recognised word, timed against the whole book rather than one file."""

    text: str
    start: float
    end: float
    probability: float


def pick_device(requested: str) -> tuple[str, str]:
    """Resolve ``auto`` into a concrete CTranslate2 device and compute type."""
    if requested in {"cuda", "cpu"}:
        device = requested
    else:
        enable_cuda_libraries()
        try:
            import ctranslate2

            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            device = "cpu"
    if device == "cuda":
        enable_cuda_libraries()
    return device, ("float16" if device == "cuda" else "int8")


def load_model(name: str, device: str, compute_type: str):
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover - dependency is declared, but be explicit
        raise DependencyError(f"faster-whisper is not installed: {exc}") from exc
    try:
        return WhisperModel(name, device=device, compute_type=compute_type)
    except Exception as exc:
        message = str(exc)
        if any(token in message.lower() for token in ("connection", "resolve", "network", "proxy")):
            raise DependencyError(
                f"could not download the '{name}' model. readalign needs network access once to "
                f"fetch it, then works offline. Underlying error: {message}"
            ) from exc
        raise DependencyError(f"could not load the '{name}' model: {message}") from exc


CUDA_HINT = (
    "install the CUDA runtime wheels with "
    "'pip install nvidia-cublas-cu12 nvidia-cudnn-cu12', or use --device cpu"
)


def _is_cuda_failure(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(token in message for token in ("cuda", "cublas", "cudnn", "gpu"))


@dataclass
class _Settings:
    """The recogniser knobs, carried together so the per-track call stays readable."""

    model_name: str
    language: str | None
    vad: bool
    beam_size: int
    window_seconds: float


def _transcribe_track(model, track, count, settings, *, before, total, started, log) -> list[Word]:
    """Transcribe one track, in windows, returning words relative to the start of that track."""
    from .audio import plan_windows

    words: list[Word] = []
    windows = plan_windows(track, settings.window_seconds)
    for window_index, (start, end) in enumerate(windows, start=1):
        audio = read_wav_segment(track.wav_path, start, end)
        suffix = f" window {window_index}/{len(windows)}" if len(windows) > 1 else ""
        log(f"  [{track.index}/{count}] {os.path.basename(track.source)}{suffix}: "
            f"transcribing {_hms(end - start)}")
        segments, detected = model.transcribe(
            audio,
            language=settings.language,
            word_timestamps=True,
            vad_filter=settings.vad,
            beam_size=settings.beam_size,
            condition_on_previous_text=False,
        )
        for segment in segments:
            for word in segment.words or []:
                text = word.word.strip()
                if text:
                    # float() because CTranslate2 hands back numpy scalars, which would
                    # otherwise travel all the way into the JSON report and fail to encode.
                    words.append(
                        Word(text, start + float(word.start), start + float(word.end),
                             float(word.probability))
                    )
        settings.language = settings.language or detected.language
        elapsed = time.monotonic() - started
        log(f"      {len(words)} words so far, {_hms(before + end)}/{_hms(total)} "
            f"audio in {_hms(elapsed)} ({(before + end) / max(elapsed, 0.001):.1f}x realtime)")
    return words


def _cache_key(track: AudioTrack, model: str, language: str | None, vad: bool) -> str:
    stat = os.stat(track.path)
    payload = "|".join(
        [os.path.basename(track.source), str(stat.st_size), f"{track.duration:.3f}",
         model, language or "auto", str(vad)]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def _load_cached(cache_dir: str | None, key: str) -> list[Word] | None:
    if not cache_dir:
        return None
    path = os.path.join(cache_dir, f"{key}.json")
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    return [Word(**entry) for entry in data]


def _store_cached(cache_dir: str | None, key: str, words: list[Word]) -> None:
    if not cache_dir:
        return
    try:
        os.makedirs(cache_dir, exist_ok=True)
        path = os.path.join(cache_dir, f"{key}.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump([asdict(word) for word in words], handle)
    except OSError as exc:
        print(f"  warning: could not write the transcript cache: {exc}", file=sys.stderr)


def transcribe_tracks(
    tracks: list[AudioTrack],
    *,
    model_name: str,
    device: str = "auto",
    language: str | None = None,
    vad: bool = True,
    beam_size: int = 5,
    window_seconds: float = 3600.0,
    cache_dir: str | None = None,
    log=print,
) -> tuple[list[Word], dict]:
    """Transcribe every track and return the words in absolute book time."""
    resolved_device, compute_type = pick_device(device)
    can_fall_back = resolved_device == "cuda" and device != "cuda"
    settings = _Settings(model_name, language, vad, beam_size, window_seconds)
    words: list[Word] = []
    model = None
    info = {"device": resolved_device, "compute_type": compute_type, "model": model_name,
            "cached_tracks": 0, "language": language}
    started = time.monotonic()
    audio_total = sum(track.duration for track in tracks) or 1.0
    done = 0.0

    for track in tracks:
        key = _cache_key(track, model_name, language, vad) if cache_dir else ""
        cached = _load_cached(cache_dir, key)
        if cached is not None:
            log(f"  [{track.index}/{len(tracks)}] {os.path.basename(track.source)}: "
                f"{len(cached)} words from cache")
            words.extend(
                Word(w.text, w.start + track.offset, w.end + track.offset, w.probability)
                for w in cached
            )
            info["cached_tracks"] += 1
            done += track.duration
            continue

        while True:
            try:
                if model is None:
                    log(f"  loading whisper model '{model_name}' on "
                        f"{resolved_device} ({compute_type})")
                    model = load_model(model_name, resolved_device, compute_type)
                track_words = _transcribe_track(
                    model, track, len(tracks), settings,
                    before=done, total=audio_total, started=started, log=log,
                )
                break
            except (DependencyError, RuntimeError) as exc:
                # CTranslate2 only touches the GPU on the first encode, so a broken CUDA
                # install surfaces here and not when the model was constructed.
                if not _is_cuda_failure(exc):
                    raise
                if not can_fall_back:
                    raise DependencyError(f"the GPU could not be used: {exc}. {CUDA_HINT}") from exc
                log(f"  warning: the GPU could not be used ({exc}), falling back to the CPU. "
                    f"To use the GPU, {CUDA_HINT}.")
                can_fall_back = False
                model = None
                resolved_device, compute_type = "cpu", "int8"
                info["device"], info["compute_type"] = resolved_device, compute_type
                info["cuda_fallback"] = str(exc)
        _store_cached(cache_dir, key, track_words)
        words.extend(
            Word(w.text, w.start + track.offset, w.end + track.offset, w.probability)
            for w in track_words
        )
        done += track.duration

    words.sort(key=lambda word: (word.start, word.end))
    info["language"] = settings.language
    info["words"] = len(words)
    info["seconds"] = round(time.monotonic() - started, 1)
    return words, info


def _hms(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"
