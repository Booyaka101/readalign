"""The recogniser's device handling: a broken CUDA install must not end in a traceback."""

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
