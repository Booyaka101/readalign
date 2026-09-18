"""Filesystem writes that fail with a sentence instead of a traceback."""

from __future__ import annotations

import os

from .errors import InputError


def ensure_directory(path: str, what: str = "output directory") -> None:
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as exc:
        raise InputError(f"cannot create the {what} {path}: {exc}") from exc


def write_text(path: str, text: str) -> None:
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    except OSError as exc:
        raise InputError(f"cannot write {path}: {exc}") from exc
