"""What an output archive member can be, and how to read one.

An audiobook is three orders of magnitude larger than everything else readalign handles, so
members it does not need to look at are carried as a reference and streamed straight through to
the output rather than held as bytes.
"""

from __future__ import annotations

import zipfile
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import BinaryIO


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
