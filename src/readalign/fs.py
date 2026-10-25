"""Filesystem writes that fail with a sentence instead of a traceback."""

from __future__ import annotations

import os
import tempfile
from collections import Counter
from contextlib import contextmanager, suppress

from .errors import InputError


def ensure_directory(path: str, what: str = "output directory") -> None:
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as exc:
        raise InputError(f"cannot create the {what} {path}: {exc}") from exc


def same_path(first: str, second: str) -> bool:
    """Compare normal paths, symbolic links and existing hard links."""
    return (os.path.normcase(os.path.realpath(first)) == os.path.normcase(os.path.realpath(second))
            or (os.path.exists(first) and os.path.exists(second)
                and os.path.samefile(first, second)))


def is_within(path: str, directory: str) -> bool:
    """Include the directory itself and resolve aliases before comparing path components."""
    path = os.path.normcase(os.path.realpath(path))
    directory = os.path.normcase(os.path.realpath(directory))
    try:
        return os.path.commonpath([path, directory]) == directory
    except ValueError:  # Windows paths can be on different drives.
        return False


def write_text(path: str, text: str) -> None:
    try:
        with atomic_output(path) as temporary, \
                open(temporary, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    except OSError as exc:
        raise InputError(f"cannot write {path}: {exc}") from exc


@contextmanager
def atomic_output(path: str):
    """Replace a destination only after a complete write on the same filesystem."""
    fd, temporary = tempfile.mkstemp(prefix=".readalign-", suffix=".tmp",
                                     dir=os.path.dirname(os.path.abspath(path)))
    os.close(fd)
    try:
        yield temporary
        os.replace(temporary, path)
    finally:
        with suppress(OSError):
            os.unlink(temporary)


def unique_filenames(names: list[str]) -> list[str]:
    """Disambiguate leaf names without taking another entry's original name."""
    counts = Counter(name.casefold() for name in names)
    used = set(counts)
    result = []
    for index, name in enumerate(names, start=1):
        if counts[name.casefold()] > 1:
            stem, extension = os.path.splitext(name)
            suffix = index
            while f"{stem}.{suffix}{extension}".casefold() in used:
                suffix += 1
            name = f"{stem}.{suffix}{extension}"
            used.add(name.casefold())
        result.append(name)
    return result
