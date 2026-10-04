"""The recogniser: device handling when CUDA is broken, and the transcript cache."""

import json

import pytest

from conftest import make_tracks
from readalign import asr
from readalign.errors import DependencyError

CUBLAS = "Library cublas64_12.dll is not found or cannot be loaded"


def gpu_that_fails(monkeypatch, failures):
    """Pretend CUDA is available, then fail the first ``failures`` transcription attempts."""
    attempts = []

    def transcribe(model, track, count, settings, **_):
        attempts.append(track.index)
        if len(attempts) <= failures:
            raise RuntimeError(CUBLAS)
        return [asr.Word("hello", 0.0, 0.4, 0.9)]

    monkeypatch.setattr(asr, "pick_device", lambda requested: ("cuda", "float16"))
    monkeypatch.setattr(asr, "load_model", lambda *a: object())
    monkeypatch.setattr(asr, "_transcribe_track", transcribe)
    return attempts


def test_a_broken_cuda_install_falls_back_to_the_cpu(monkeypatch, capsys):
    attempts = gpu_that_fails(monkeypatch, failures=1)
    words, info = asr.transcribe_tracks(
        make_tracks([10.0]), model_name="tiny", device="auto", log=print
    )
    assert len(words) == 1
    assert info["device"] == "cpu"
    assert CUBLAS in info["cuda_fallback"]
    assert attempts == [1, 1]
    assert "falling back to the CPU" in capsys.readouterr().out


def test_device_cuda_says_what_to_install_instead_of_falling_back(monkeypatch):
    gpu_that_fails(monkeypatch, failures=1)
    with pytest.raises(DependencyError) as caught:
        asr.transcribe_tracks(
            make_tracks([10.0]), model_name="tiny", device="cuda", log=lambda *_: None
        )
    assert "nvidia-cublas-cu12" in str(caught.value)
    assert caught.value.exit_code == 1


def test_a_failure_that_is_not_cuda_is_not_retried(monkeypatch):
    def transcribe(*_a, **_k):
        raise RuntimeError("the wav file went missing")

    monkeypatch.setattr(asr, "pick_device", lambda requested: ("cuda", "float16"))
    monkeypatch.setattr(asr, "load_model", lambda *a: object())
    monkeypatch.setattr(asr, "_transcribe_track", transcribe)
    with pytest.raises(RuntimeError, match="wav file went missing"):
        asr.transcribe_tracks(
            make_tracks([10.0]), model_name="tiny", device="auto", log=lambda *_: None
        )


def recogniser(monkeypatch, detected="en"):
    """Stand in for the model: one word per track, latching a detected language as the real one."""

    def transcribe(model, track, count, settings, **_):
        settings.language = settings.language or detected
        return [asr.Word("hello", 0.0, 0.4, 0.9)]

    monkeypatch.setattr(asr, "pick_device", lambda requested: ("cpu", "int8"))
    monkeypatch.setattr(asr, "load_model", lambda *a: object())
    monkeypatch.setattr(asr, "_transcribe_track", transcribe)


def one_track(tmp_path):
    tracks = make_tracks([10.0])
    audio = tmp_path / "part001.mp3"
    audio.write_bytes(b"not really an mp3, but it has to have a size")
    tracks[0].path = str(audio)
    return tracks


def run(tracks, cache):
    return asr.transcribe_tracks(
        tracks, model_name="tiny", device="auto", cache_dir=str(cache), log=lambda *_: None
    )


def test_a_cached_run_reports_the_language_the_first_run_detected(monkeypatch, tmp_path):
    """The report's asr.language must not go null just because nothing needed transcribing."""
    recogniser(monkeypatch)
    tracks = one_track(tmp_path)
    cache = tmp_path / "cache"
    _, first = run(tracks, cache)
    assert (first["cached_tracks"], first["language"]) == (0, "en")
    words, second = run(tracks, cache)
    assert (second["cached_tracks"], second["language"]) == (1, "en")
    assert [word.text for word in words] == ["hello"]


def test_the_cache_key_covers_every_setting_that_changes_the_transcript(tmp_path):
    """A different beam size or window plan transcribes differently, so the key must differ."""
    track = one_track(tmp_path)[0]
    base = asr._cache_key(track, "tiny", None, True, 5, 3600.0)
    assert asr._cache_key(track, "tiny", None, True, 5, 3600.0) == base
    assert asr._cache_key(track, "tiny", None, True, 1, 3600.0) != base
    assert asr._cache_key(track, "tiny", None, True, 5, 1800.0) != base
    assert asr._cache_key(track, "tiny", "de", True, 5, 3600.0) != base
    assert asr._cache_key(track, "tiny", None, False, 5, 3600.0) != base


def test_a_cache_write_failure_is_reported_through_the_log(tmp_path, capsys):
    """--quiet swaps the log for a no-op, so cache warnings must not print behind its back."""
    cache = tmp_path / "cache"
    cache.write_bytes(b"")  # a file where the cache directory should be
    asr._store_cached(str(cache), "key", [asr.Word("x", 0.0, 0.1, 1.0)], None, log=print)
    assert "could not write the transcript cache" in capsys.readouterr().out
    # And it stays quiet, and harmless, when the log is the quiet no-op.
    asr._store_cached(str(cache), "key", [], None, log=lambda *_: None)


def _written_before_languages(data):
    """A cache from an older readalign: a bare list of words with no language beside them."""
    return data["words"]


def _unreadable(data):
    """An entry whose words no longer match the fields Word takes."""
    return {"words": [{"nope": 1}]}


@pytest.mark.parametrize(
    ("mangle", "cached_tracks"), [(_written_before_languages, 1), (_unreadable, 0)]
)
def test_a_cache_entry_that_is_old_or_broken(monkeypatch, tmp_path, mangle, cached_tracks):
    """An older entry still reads; an unreadable one is transcribed again rather than raising."""
    recogniser(monkeypatch)
    tracks = one_track(tmp_path)
    cache = tmp_path / "cache"
    run(tracks, cache)
    entry = next(iter(cache.glob("*.json")))
    entry.write_text(json.dumps(mangle(json.loads(entry.read_text()))), encoding="utf-8")
    words, info = run(tracks, cache)
    assert info["cached_tracks"] == cached_tracks
    assert [word.text for word in words] == ["hello"]
    assert info["language"] == (None if cached_tracks else "en")
