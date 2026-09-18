"""Optional CTC forced-alignment pass that sharpens sentence boundaries.

Whisper's word timestamps come from attention weights and are typically good to a few tenths of
a second. A frame-level CTC aligner does better, so ``--refine`` runs torchaudio's MMS_FA bundle
over each already-aligned stretch and replaces the boundaries it is confident about.

The MMS-300m aligner is licensed cc-by-nc-4.0, which is not a licence that belongs in a default
install path, so this module is never imported unless ``--refine`` is passed and the model is
never downloaded otherwise.
"""

from __future__ import annotations

from .align import AlignmentResult, TimedSentence
from .audio import SAMPLE_RATE, AudioTrack, read_wav_segment
from .errors import DependencyError
from .textnorm import tokenize_words

#: Seconds of audio handed to the aligner at once. The CTC lattice is time x tokens, so this
#: bounds peak memory independently of how long the book is.
CHUNK_SECONDS = 40.0

#: Context added either side of a chunk so a boundary word is never cut in half.
PAD_SECONDS = 0.5

#: Boundaries are only replaced when the new value is within this many seconds of the old one.
MAX_SHIFT = 2.0

LICENCE_NOTE = (
    "the MMS-300m forced aligner is licensed CC BY-NC 4.0 (non-commercial). "
    "Output timings refined with it inherit that restriction."
)


def _load_bundle(device: str):
    try:
        import torch
        import torchaudio
    except ImportError as exc:
        raise DependencyError(
            "--refine needs the optional extra: pip install 'readalign[refine]'"
        ) from exc
    try:
        bundle = torchaudio.pipelines.MMS_FA
        model = bundle.get_model()
    except Exception as exc:
        message = str(exc)
        raise DependencyError(
            f"could not load the MMS forced aligner. It is downloaded once from "
            f"download.pytorch.org, so this needs network access. Underlying error: {message}"
        ) from exc
    resolved = device
    if device == "auto":
        resolved = "cuda" if torch.cuda.is_available() else "cpu"
    model = model.to(resolved).eval()
    return torch, bundle, model, resolved


def _chunks(entries: list[TimedSentence]) -> list[list[TimedSentence]]:
    chunks: list[list[TimedSentence]] = []
    current: list[TimedSentence] = []
    for entry in entries:
        if current and entry.end - current[0].start > CHUNK_SECONDS:
            chunks.append(current)
            current = []
        current.append(entry)
    if current:
        chunks.append(current)
    return chunks


def _transcript(
    entries: list[TimedSentence], vocabulary: set[str]
) -> tuple[list[str], list[tuple[int, int]]]:
    """Flatten the chunk's sentences into aligner words, keeping each sentence's word range."""
    words: list[str] = []
    spans: list[tuple[int, int]] = []
    for entry in entries:
        tokens, _ = tokenize_words(entry.sentence.words)
        start = len(words)
        for token in tokens:
            cleaned = "".join(character for character in token if character in vocabulary)
            if cleaned:
                words.append(cleaned)
        spans.append((start, len(words)))
    return words, spans


def refine(
    result: AlignmentResult,
    tracks: list[AudioTrack],
    *,
    device: str = "auto",
    log=print,
) -> dict:
    """Replace Whisper's sentence boundaries with CTC ones wherever both agree roughly."""
    torch, bundle, model, resolved = _load_bundle(device)
    tokenizer = bundle.get_tokenizer()
    aligner = bundle.get_aligner()
    vocabulary = set(bundle.get_dict())
    by_index = {track.index: track for track in tracks}
    log(f"  refining boundaries with MMS_FA on {resolved}")

    adjusted = 0
    failed = 0
    total_shift = 0.0
    for track_index, track in sorted(by_index.items()):
        if track.wav_path is None:
            continue
        entries = sorted(
            (entry for entry in result.timed if entry.track_index == track_index),
            key=lambda entry: entry.start,
        )
        if not entries:
            continue
        for chunk in _chunks(entries):
            words, spans = _transcript(chunk, vocabulary)
            if not words:
                continue
            begin = max(0.0, chunk[0].start - track.offset - PAD_SECONDS)
            finish = min(track.duration, chunk[-1].end - track.offset + PAD_SECONDS)
            if finish - begin < 0.05:
                continue
            samples = read_wav_segment(track.wav_path, begin, finish)
            if samples.size < SAMPLE_RATE // 10:
                continue
            waveform = torch.from_numpy(samples).unsqueeze(0).to(resolved)
            try:
                with torch.inference_mode():
                    emission, _ = model(waveform)
                    token_spans = aligner(emission[0], tokenizer(words))
            except Exception as exc:
                failed += 1
                if failed == 1:
                    log(f"  warning: the refiner failed on one chunk and was skipped: {exc}")
                continue
            ratio = waveform.size(1) / emission.size(1)
            origin = begin + track.offset
            starts = [origin + span[0].start * ratio / SAMPLE_RATE for span in token_spans]
            ends = [origin + span[-1].end * ratio / SAMPLE_RATE for span in token_spans]
            for entry, (first, last) in zip(chunk, spans, strict=True):
                if last <= first or last > len(starts):
                    continue
                new_start, new_end = starts[first], ends[last - 1]
                if new_end <= new_start:
                    continue
                if abs(new_start - entry.start) > MAX_SHIFT or abs(new_end - entry.end) > MAX_SHIFT:
                    continue
                total_shift += abs(new_start - entry.start)
                entry.start, entry.end = new_start, new_end
                entry.interpolated = False
                adjusted += 1

    _repair_order(result.timed, by_index)
    skipped = f", {failed} chunk(s) skipped" if failed else ""
    log(f"  refined {adjusted} sentence boundaries{skipped}")
    return {
        "device": resolved,
        "adjusted": adjusted,
        "failed_chunks": failed,
        "mean_shift": round(total_shift / adjusted, 3) if adjusted else 0.0,
        "licence": LICENCE_NOTE,
    }


def _repair_order(timed: list[TimedSentence], tracks: dict[int, AudioTrack]) -> None:
    """Keep the sequence monotonic and inside each track after boundaries moved."""
    previous: float | None = None
    for entry in timed:
        track = tracks[entry.track_index]
        entry.start = min(max(entry.start, track.offset), track.end)
        entry.end = min(max(entry.end, entry.start + 0.05), track.end)
        if previous is not None and entry.start < previous:
            entry.start = min(previous, entry.end - 0.05)
        previous = entry.end
