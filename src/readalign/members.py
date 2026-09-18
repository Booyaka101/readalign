"""What an output archive member can be, and how to read one.

An audiobook is three orders of magnitude larger than everything else readalign handles, so
members it does not need to look at are carried as a reference and streamed straight through to
the output rather than held as bytes.
"""

from __future__ import annotations

import os
import zipfile
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import BinaryIO

from .errors import InputError


@dataclass(frozen=True)
class SourceFile:
    """A member whose bytes are a file on disk, such as a prepared audio track."""

    path: str


@dataclass(frozen=True)
class ArchiveMember:
    """A member copied through from the input EPUB without being parsed or changed."""

    epub_path: str
    name: str


#: An output archive as member name to content.
Member = dict[str, "bytes | SourceFile | ArchiveMember"]


def open_archive(path: str) -> zipfile.ZipFile:
    """Open an EPUB for reading, turning every unreadable-path case into a clear message."""
    try:
        return zipfile.ZipFile(path)
    except FileNotFoundError:
        raise InputError(f"no such file: {path}") from None
    except (IsADirectoryError, PermissionError) as exc:
        # Windows raises PermissionError for a directory, where POSIX raises IsADirectoryError.
        if os.path.isdir(path):
            raise InputError(f"not an .epub file but a directory: {path}") from None
        raise InputError(f"cannot read {path}: {exc.strerror or exc}") from None
    except zipfile.BadZipFile as exc:
        raise InputError(f"{path} is not a readable EPUB (not a zip archive): {exc}") from None
    except OSError as exc:
        raise InputError(f"cannot read {path}: {exc.strerror or exc}") from None

class MemberReader(AbstractContextManager):
    """Opens by-reference members, holding each source archive open only once."""

    def __init__(self) -> None:
        self._archives: dict[str, zipfile.ZipFile] = {}

    def open(self, member: SourceFile | ArchiveMember) -> BinaryIO:
        if isinstance(member, SourceFile):
            return open(member.path, "rb")
        archive = self._archives.get(member.epub_path)
        if archive is None:
            archive = zipfile.ZipFile(member.epub_path)
            self._archives[member.epub_path] = archive
        return archive.open(member.name)

    def __exit__(self, *_) -> None:
        while self._archives:
            _, archive = self._archives.popitem()
            archive.close()
