"""Fetch the public-domain sample pairs readalign is demonstrated and tested with.

    python scripts/fetch_samples.py short   # 2 minutes of audio, builds examples/aesop
    python scripts/fetch_samples.py long    # 7.5 hours of Frankenstein, into a directory

Nothing here is needed to use readalign. It exists so the examples in the README can be
reproduced from scratch, and so the long-pair regression run does not need a 200 MB checkout.
"""

from __future__ import annotations

import argparse
import html
import os
import sys
import urllib.error
import urllib.request
import zipfile

USER_AGENT = "readalign-sample-fetcher/1.0 (+https://pypi.org/project/readalign/)"

AESOP_AUDIO = (
    "https://archive.org/download/aesopforchildren_1308_librivox/"
    "theaesopforchildren_37_aesop_64kb.mp3"
)
AESOP_TEXT = "https://www.gutenberg.org/cache/epub/19994/pg19994.txt"

#: Section 37 of the LibriVox recording is exactly these two fables.
AESOP_FABLES = ["THE FISHERMAN AND THE LITTLE FISH", "THE FIGHTING COCKS AND THE EAGLE"]

FRANKENSTEIN_EPUB = (
    "https://standardebooks.org/ebooks/mary-shelley/frankenstein/downloads/"
    "mary-shelley_frankenstein.epub?source=download"
)
FRANKENSTEIN_AUDIO = "https://archive.org/download/frankenstein_1107_librivox/"
FRANKENSTEIN_TRACKS = [f"frankenstein_{index:02d}_shelley_64kb.mp3" for index in range(25)]


def download(url: str, path: str) -> str:
    if os.path.exists(path) and os.path.getsize(path) > 0:
        print(f"  have {os.path.basename(path)}")
        return path
    print(f"  get  {os.path.basename(path)}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=120) as response, open(path, "wb") as handle:
            while chunk := response.read(1 << 16):
                handle.write(chunk)
    except (urllib.error.URLError, TimeoutError) as exc:
        if os.path.exists(path):
            os.unlink(path)
        raise SystemExit(f"could not download {url}: {exc}") from exc
    return path


def paragraphs(block: str):
    for piece in block.split("\n\n"):
        text = " ".join(piece.split())
        if text:
            yield text.replace("_", "")


def extract_fable(text: str, title: str) -> tuple[str, list[str]]:
    rest = text[text.index(title) + len(title):]
    end = len(rest)
    for marker in ("\n\n\n\n\n", "[Illustration]", "*** END OF THE PROJECT GUTENBERG"):
        found = rest.find(marker)
        if found != -1:
            end = min(end, found)
    return title.title(), list(paragraphs(rest[:end]))


DOCUMENT = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>{title}</title></head>
<body epub:type="bodymatter">
<section epub:type="chapter">
<h2>{title}</h2>
{body}
</section>
</body>
</html>
"""

PACKAGE = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="uid">urn:uuid:8f2c1e4a-6b30-4d57-9c11-5a7e0d3b9f26</dc:identifier>
    <dc:title>Two Fables from The Aesop for Children</dc:title>
    <dc:language>en</dc:language>
    <dc:creator>Aesop</dc:creator>
    <dc:source>https://www.gutenberg.org/ebooks/19994</dc:source>
    <dc:rights>Public domain in the United States.</dc:rights>
    <meta property="dcterms:modified">2026-01-01T00:00:00Z</meta>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
{items}
  </manifest>
  <spine>
{spine}
  </spine>
</package>
"""

NAV = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>Contents</title></head>
<body>
<nav epub:type="toc"><h1>Contents</h1><ol>
{entries}
</ol></nav>
</body>
</html>
"""

CONTAINER = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="EPUB/package.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""


def build_epub(path: str, chapters: list[tuple[str, list[str]]]) -> str:
    documents = {}
    items, spine, entries = [], [], []
    for index, (title, body) in enumerate(chapters, start=1):
        name = f"chapter{index}.xhtml"
        markup = "\n".join(f"<p>{html.escape(line)}</p>" for line in body)
        documents[f"EPUB/{name}"] = DOCUMENT.format(title=html.escape(title), body=markup)
        items.append(f'    <item id="c{index}" href="{name}" media-type="application/xhtml+xml"/>')
        spine.append(f'    <itemref idref="c{index}"/>')
        entries.append(f'<li><a href="{name}">{html.escape(title)}</a></li>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo("mimetype")
        info.compress_type = zipfile.ZIP_STORED
        archive.writestr(info, b"application/epub+zip")
        archive.writestr("META-INF/container.xml", CONTAINER)
        archive.writestr(
            "EPUB/package.opf", PACKAGE.format(items="\n".join(items), spine="\n".join(spine))
        )
        archive.writestr("EPUB/nav.xhtml", NAV.format(entries="\n".join(entries)))
        for name, data in documents.items():
            archive.writestr(name, data)
    return path


def fetch_short(directory: str) -> None:
    os.makedirs(directory, exist_ok=True)
    audio = download(AESOP_AUDIO, os.path.join(directory, "aesop-section-37.mp3"))
    raw = download(AESOP_TEXT, os.path.join(directory, "pg19994.txt"))
    with open(raw, encoding="utf-8-sig") as handle:
        text = handle.read()
    missing = [title for title in AESOP_FABLES if title not in text]
    if missing:
        raise SystemExit(f"the Gutenberg text no longer contains: {', '.join(missing)}")
    epub = build_epub(
        os.path.join(directory, "aesop-two-fables.epub"),
        [extract_fable(text, title) for title in AESOP_FABLES],
    )
    os.unlink(raw)
    print(f"\n  audio {audio}\n  ebook {epub}")
    print("\nbuild it with:\n")
    print(f"  readalign build --audio {audio} --epub {epub} \\")
    print("      --out aligned.epub --model tiny.en --language en")


def fetch_long(directory: str) -> None:
    audio_dir = os.path.join(directory, "frankenstein-audio")
    os.makedirs(audio_dir, exist_ok=True)
    epub = download(FRANKENSTEIN_EPUB, os.path.join(directory, "frankenstein-se.epub"))
    for name in FRANKENSTEIN_TRACKS:
        download(FRANKENSTEIN_AUDIO + name, os.path.join(audio_dir, name))
    print(f"\n  audio {audio_dir}\n  ebook {epub}")
    print("\nbuild it with:\n")
    print(f"  readalign build --audio {audio_dir} --epub {epub} \\")
    print("      --out frankenstein-aligned.epub --language en")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("which", choices=["short", "long"])
    parser.add_argument("--out", default=None, help="where to put the files")
    args = parser.parse_args(argv)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if args.which == "short":
        fetch_short(args.out or os.path.join(root, "examples", "aesop"))
    else:
        fetch_long(args.out or os.path.join(root, "sample-data"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
