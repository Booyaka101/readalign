"""Write one media overlay document per content document.

Structure follows EPUB 3.3 section 9.2.2: a ``smil`` root at version 3.0, a ``body`` carrying
``par`` elements, and a ``seq`` wrapper wherever the text is something a reading system may let
the user skip (a footnote, a page number).
"""

from __future__ import annotations

import posixpath

from lxml import etree

from .align import TimedSentence
from .audio import AudioTrack
from .clock import format_clock

SMIL_NS = "http://www.w3.org/ns/SMIL"
EPUB_NS = "http://www.idpf.org/2007/ops"

_NSMAP = {None: SMIL_NS, "epub": EPUB_NS}


def relative_href(from_path: str, to_path: str) -> str:
    """Path from one archive member to another, as a path-relative-scheme-less-URL string."""
    relative = posixpath.relpath(to_path, posixpath.dirname(from_path))
    return relative.replace("\\", "/")


def build_smil(
    smil_path: str,
    doc_path: str,
    entries: list[TimedSentence],
    tracks: dict[int, AudioTrack],
    audio_paths: dict[int, str],
) -> bytes:
    """Serialise the overlay for one content document."""
    text_href = relative_href(smil_path, doc_path)
    root = etree.Element(f"{{{SMIL_NS}}}smil", nsmap=_NSMAP)
    root.set("version", "3.0")
    body = etree.SubElement(root, f"{{{SMIL_NS}}}body")
    body.set(f"{{{EPUB_NS}}}textref", text_href)

    container: etree._Element = body
    current_group: str | None = None
    for entry in entries:
        group = entry.sentence.container_id if entry.sentence.epub_type else None
        if group != current_group:
            current_group = group
            if group is None:
                container = body
            else:
                container = etree.SubElement(body, f"{{{SMIL_NS}}}seq")
                container.set(f"{{{EPUB_NS}}}textref", f"{text_href}#{group}")
                container.set(f"{{{EPUB_NS}}}type", entry.sentence.epub_type)
        par = etree.SubElement(container, f"{{{SMIL_NS}}}par")
        text = etree.SubElement(par, f"{{{SMIL_NS}}}text")
        text.set("src", f"{text_href}#{entry.sentence.fragment_id}")
        track = tracks[entry.track_index]
        audio = etree.SubElement(par, f"{{{SMIL_NS}}}audio")
        audio.set("src", relative_href(smil_path, audio_paths[entry.track_index]))
        audio.set("clipBegin", format_clock(entry.start - track.offset))
        audio.set("clipEnd", format_clock(entry.end - track.offset))
    return etree.tostring(root, xml_declaration=True, encoding="utf-8", pretty_print=True)


def overlay_duration(entries: list[TimedSentence]) -> float:
    """Playing time of an overlay: the sum of its clips, which is what media:duration means."""
    return sum(max(0.0, entry.end - entry.start) for entry in entries)
