"""Error types carrying the process exit code readalign should terminate with."""

from __future__ import annotations


class ReadAlignError(Exception):
    """Base for every error readalign turns into a one-line message instead of a traceback."""

    exit_code = 1


class InputError(ReadAlignError):
    """A file the user pointed at is missing, empty or not the kind of file it claims to be."""


class DRMError(ReadAlignError):
    """The input is DRM-protected. readalign refuses these by design."""

    exit_code = 2


class DependencyError(ReadAlignError):
    """An external prerequisite (ffmpeg, a model download) is unavailable."""


class AlignmentError(ReadAlignError):
    """Nothing in the book could be matched to the narration."""
