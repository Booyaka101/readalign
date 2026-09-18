"""Write one media overlay document per content document.

Structure follows EPUB 3.3 section 9.2.2: a ``smil`` root at version 3.0, a ``body`` carrying
``par`` elements, and nested ``seq`` wrappers mirroring the structures a reading system may let
the user skip (a footnote, a page number) or escape out of (a table, a list, a figure).
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

    # Open seqs, innermost last. Nesting has to mirror the document for escapability to work,
    # and entries arrive in document order, so a structure is left as soon as its ids stop matching.
    open_seqs: list[tuple[str, etree._Element]] = []
    for entry in entries:
        chain = entry.sentence.structure
        shared = 0
        while (
            shared < len(open_seqs)
            and shared < len(chain)
            and open_seqs[shared][0] == chain[shared].container_id
        ):
            shared += 1
        del open_seqs[shared:]
        for level in chain[shared:]:
            parent = open_seqs[-1][1] if open_seqs else body
            seq = etree.SubElement(parent, f"{{{SMIL_NS}}}seq")
            seq.set(f"{{{EPUB_NS}}}textref", f"{text_href}#{level.container_id}")
            seq.set(f"{{{EPUB_NS}}}type", level.epub_type)
            open_seqs.append((level.container_id, seq))
        container = open_seqs[-1][1] if open_seqs else body
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
