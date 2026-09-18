"""Fixtures: a minimal but valid EPUB 3, and a transcript built from known sentence timings."""

from __future__ import annotations

import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

CONTAINER = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="EPUB/package.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

DOC = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>{title}</title></head>
<body>{body}</body>
</html>
"""

CHAPTERS = [
    (
        "Chapter One",
        "<h1>Chapter One</h1>"
        "<p>The ship left the harbour at dawn. Nobody on the quay waved goodbye. "
        "A cold wind came down from the north.</p>"
        "<p>By noon the coast was a grey line behind us.</p>",
    ),
    (
        "Chapter Two",
        "<h1>Chapter Two</h1>"
        "<p>We sighted ice on the ninth day.<a epub:type=\"noteref\" href=\"#fn1\">1</a> "
        "The captain ordered the sails reefed.</p>"
        "<aside epub:type=\"footnote\" id=\"fn1\"><p>Ice that far south was unusual.</p></aside>"
        "<p>Then the fog closed over everything.</p>",
    ),
]


#: A chapter whose narratable text sits inside structures an overlay has to mirror.
STRUCTURED = (
    "<p>The ship left the harbour at dawn.</p>"
    "<table><tr><td>The first cell holds a sentence.</td>"
    "<td>The second cell holds another one.</td></tr></table>"
    "<ul><li>The first item stands alone.</li>"
    "<li><p>The second item carries more weight.</p>"
    "<ol><li>A nested item goes here.</li></ol></li></ul>"
    "<figure><figcaption>A caption for the plate.</figcaption></figure>"
    "<p>Then the fog closed over everything.</p>"
)


def build_epub(path: str, chapters=CHAPTERS, *, version: str = "3.0", extra: dict | None = None):
    """Write a small valid EPUB 3 and return its path."""
    manifest = []
    spine = []
    documents = {}
    for index, (title, body) in enumerate(chapters, start=1):
        name = f"chapter{index}.xhtml"
        documents[f"EPUB/{name}"] = DOC.format(title=title, body=body).encode("utf-8")
        manifest.append(
            f'<item id="c{index}" href="{name}" media-type="application/xhtml+xml"/>'
        )
        spine.append(f'<itemref idref="c{index}"/>')
    opf = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="{version}" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="uid">urn:uuid:readalign-test</dc:identifier>
    <dc:title>A Test Voyage</dc:title>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>{''.join(manifest)}</manifest>
  <spine>{''.join(spine)}</spine>
</package>
"""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo("mimetype")
        info.compress_type = zipfile.ZIP_STORED
        archive.writestr(info, b"application/epub+zip")
        archive.writestr("META-INF/container.xml", CONTAINER)
        archive.writestr("EPUB/package.opf", opf)
        documents.update(extra or {})
        for name, data in documents.items():
            archive.writestr(name, data)
    return path


@pytest.fixture
def sample_epub(tmp_path):
    return build_epub(str(tmp_path / "sample.epub"))


def make_tracks(durations, suffix=".mp3"):
    """AudioTrack objects with no file behind them. Alignment only reads the timeline."""
    from readalign.audio import AudioTrack

    tracks = []
    offset = 0.0
    for index, duration in enumerate(durations, start=1):
        tracks.append(
            AudioTrack(
                index=index,
                source=f"part{index:03d}{suffix}",
                path=f"part{index:03d}{suffix}",
                href=f"audio/part{index:03d}{suffix}",
                media_type="audio/mpeg",
                duration=duration,
                offset=offset,
            )
        )
        offset += duration
    return tracks


def speak(sentences, *, rate=0.35, start=0.0, gap=0.25, lead_in=()):
    """Turn sentences into Word objects as if a narrator had read them at a steady pace."""
    from readalign.asr import Word

    words = []
    moment = start
    for raw in lead_in:
        for token in raw.split():
            words.append(Word(token, moment, moment + rate, 0.9))
            moment += rate
        moment += gap
    for sentence in sentences:
        for token in sentence.words:
            words.append(Word(token, moment, moment + rate, 0.9))
            moment += rate
        moment += gap
    return words
