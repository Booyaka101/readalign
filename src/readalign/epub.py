"""Read an EPUB, find its readable text, and give every sentence an addressable fragment id.

Nothing here writes to the user's file: the package is loaded into memory, content documents are
modified in that copy, and :mod:`readalign.package` writes the result somewhere new.
"""

from __future__ import annotations

import posixpath
import re
import zipfile
from dataclasses import dataclass, field

from lxml import etree

from .errors import DRMError, InputError
from .members import ArchiveMember, Member
from .sentences import split_sentences

XHTML_NS = "http://www.w3.org/1999/xhtml"
OPF_NS = "http://www.idpf.org/2007/opf"
DC_NS = "http://purl.org/dc/elements/1.1/"
CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"
ENC_NS = "http://www.w3.org/2001/04/xmlenc#"
EPUB_NS = "http://www.idpf.org/2007/ops"

#: Encryption algorithms that mean "obfuscated font", not "DRM".
_FONT_OBFUSCATION = {
    "http://www.idpf.org/2008/embedding",
    "http://ns.adobe.com/pdf/enc#RC4SHA1",
}

_BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "body", "caption", "details", "dialog", "div",
    "dl", "dd", "dt", "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3",
    "h4", "h5", "h6", "header", "hgroup", "li", "main", "nav", "ol", "p", "section", "table",
    "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
}
_SKIP_TAGS = {"script", "style", "head", "title", "audio", "video", "iframe", "template"}
_ATOMIC_TAGS = {"svg", "math"}

#: epub:type values a reading system may let the user skip; copied onto the matching SMIL nodes.
SKIPPABLE_TYPES = {
    "footnote", "endnote", "note", "rearnote", "pagebreak", "noteref", "annotation", "sidebar",
}

#: XHTML elements a reading system may let the user escape out of, and the epub:type naming the
#: structure. The overlay mirrors these as nested seq elements, which is what escapability means.
ESCAPABLE_TAGS = {
    "table": "table",
    "tr": "table-row",
    "td": "table-cell",
    "th": "table-cell",
    "ol": "list",
    "ul": "list",
    "li": "list-item",
    "figure": "figure",
}

#: epub:type values that mark a reference, not narration. Their text is left out of the
#: sentence stream so a footnote marker cannot glue two sentences together.
_MARKER_TYPES = {"noteref", "pagebreak"}

_ID_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def _is_marker(element: etree._Element) -> bool:
    types = (element.get(f"{{{EPUB_NS}}}type") or "").split()
    return any(value in _MARKER_TYPES for value in types)


def local_name(tag: object) -> str:
    """The tag name without its namespace, or "" for comments and processing instructions."""
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def _namespace_of(element: etree._Element) -> str:
    tag = element.tag
    if isinstance(tag, str) and tag.startswith("{"):
        return tag[: tag.index("}") + 1]
    return ""


@dataclass(frozen=True)
class Structure:
    """One ancestor the overlay has to mirror, as a SMIL ``seq`` carrying these two values."""

    epub_type: str
    container_id: str


@dataclass
class Sentence:
    """One narratable unit of text and the fragment id a SMIL ``text`` element points at."""

    doc_index: int
    index: int
    fragment_id: str
    text: str
    words: list[str]
    #: Enclosing structures, outermost first, whether skippable or merely escapable.
    structure: tuple[Structure, ...] = ()


@dataclass
class ContentDoc:
    manifest_id: str
    zip_path: str
    href: str
    tree: etree._ElementTree
    sentences: list[Sentence] = field(default_factory=list)
    recovered: bool = False
    reencoded_from: str | None = None

    def serialize(self) -> bytes:
        return etree.tostring(self.tree, xml_declaration=True, encoding="utf-8")


@dataclass
class ManifestItem:
    id: str
    href: str
    media_type: str
    properties: str | None
    element: etree._Element


@dataclass
class EpubPackage:
    source: str
    #: Every member of the input archive. Parsed ones hold bytes; the rest, which includes any
    #: audio a previous run embedded, are references copied through at write time.
    files: Member
    order: list[str]
    opf_path: str
    opf_tree: etree._ElementTree
    version: str
    manifest: dict[str, ManifestItem]
    spine_ids: list[str]
    docs: list[ContentDoc]

    @property
    def opf_dir(self) -> str:
        return posixpath.dirname(self.opf_path)

    def resolve(self, href: str) -> str:
        """Turn an href relative to the package document into a path inside the archive."""
        joined = posixpath.join(self.opf_dir, href) if self.opf_dir else href
        return posixpath.normpath(joined)

    def title(self) -> str | None:
        for element in self.opf_tree.getroot().iter(f"{{{DC_NS}}}title"):
            if element.text and element.text.strip():
                return element.text.strip()
        return None


def _parse_xml(data: bytes, what: str) -> etree._ElementTree:
    parser = etree.XMLParser(resolve_entities=False, huge_tree=True)
    try:
        return etree.ElementTree(etree.fromstring(data, parser))
    except etree.XMLSyntaxError as exc:
        raise InputError(f"{what} is not well-formed XML: {exc}") from exc


def _decode_candidates(data: bytes) -> list[str]:
    match = re.search(rb'encoding=["\']([\w-]+)["\']', data[:200])
    declared = match[1].decode("ascii", "ignore") if match else None
    ordered = [declared, "utf-8", "utf-8-sig", "cp1252", "latin-1"]
    seen: list[str] = []
    for encoding in ordered:
        if encoding and encoding.lower() not in {item.lower() for item in seen}:
            seen.append(encoding)
    return seen


def _strip_declaration(text: str) -> bytes:
    return re.sub(r"^﻿?<\?xml[^>]*\?>", "", text, count=1).lstrip().encode("utf-8")


def parse_content_document(data: bytes, what: str) -> tuple[etree._ElementTree, bool, str | None]:
    """Parse an XHTML content document, surviving a mislabelled or broken encoding.

    Returns the tree, whether a recovering parse was needed, and the encoding that finally
    worked when it was not the declared one.
    """
    parser = etree.XMLParser(resolve_entities=False, huge_tree=True)
    try:
        return etree.ElementTree(etree.fromstring(data, parser)), False, None
    except etree.XMLSyntaxError:
        pass
    for encoding in _decode_candidates(data):
        try:
            text = data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        try:
            tree = etree.ElementTree(etree.fromstring(_strip_declaration(text), parser))
        except etree.XMLSyntaxError:
            continue
        return tree, False, encoding
    recovering = etree.XMLParser(resolve_entities=False, recover=True, huge_tree=True)
    for encoding in _decode_candidates(data):
        try:
            text = data.decode(encoding, errors="replace")
        except LookupError:
            continue
        root = etree.fromstring(_strip_declaration(text), recovering)
        if root is not None:
            return etree.ElementTree(root), True, encoding
    raise InputError(f"{what} could not be parsed as XHTML in any known encoding")


def _check_drm(files: dict[str, bytes]) -> None:
    if "META-INF/rights.xml" in files:
        raise DRMError("readalign does not handle DRM-protected files")
    encryption = files.get("META-INF/encryption.xml")
    if encryption is None:
        return
    try:
        root = etree.fromstring(encryption, etree.XMLParser(resolve_entities=False))
    except etree.XMLSyntaxError:
        raise DRMError("readalign does not handle DRM-protected files") from None
    for method in root.iter(f"{{{ENC_NS}}}EncryptionMethod"):
        if method.get("Algorithm") not in _FONT_OBFUSCATION:
            raise DRMError("readalign does not handle DRM-protected files")


def load_epub(path: str) -> EpubPackage:
    """Load an EPUB 3 package and annotate every XHTML document in its spine."""
    if path.lower().endswith(".acsm"):
        raise DRMError("readalign does not handle DRM-protected files")
    try:
        with zipfile.ZipFile(path) as archive:
            return _load_open(path, archive)
    except FileNotFoundError:
        raise InputError(f"epub not found: {path}") from None
    except IsADirectoryError:
        raise InputError(f"--epub must be an .epub file, not a directory: {path}") from None
    except zipfile.BadZipFile:
        raise InputError(f"not a readable EPUB (the file is not a zip archive): {path}") from None
    except PermissionError:
        raise InputError(f"cannot read {path}: permission denied") from None


def _load_open(path: str, archive: zipfile.ZipFile) -> EpubPackage:
    order = archive.namelist()
    files: Member = {
        name: ArchiveMember(path, name) for name in order if not name.endswith("/")
    }
    for name in files:
        if name.startswith("META-INF/"):
            files[name] = archive.read(name)

    _check_drm(files)

    container = files.get("META-INF/container.xml")
    if container is None:
        raise InputError(f"not an EPUB: {path} has no META-INF/container.xml")
    rootfiles = list(_parse_xml(container, "META-INF/container.xml")
                     .getroot().iter(f"{{{CONTAINER_NS}}}rootfile"))
    opf_path = next(
        (
            element.get("full-path")
            for element in rootfiles
            if element.get("media-type") == "application/oebps-package+xml"
            or (element.get("full-path") or "").endswith(".opf")
        ),
        None,
    )
    if not opf_path:
        raise InputError("META-INF/container.xml names no package document")
    if opf_path not in files:
        raise InputError(f"package document {opf_path} is missing from the archive")
    files[opf_path] = archive.read(opf_path)

    opf_tree = _parse_xml(files[opf_path], opf_path)
    root = opf_tree.getroot()
    version = root.get("version", "")
    if not version.startswith("3"):
        raise InputError(
            f"{path} is an EPUB {version or '2'} package; media overlays need EPUB 3. "
            "Convert it to EPUB 3 first (Calibre can do this)."
        )

    manifest: dict[str, ManifestItem] = {}
    for element in root.iter(f"{{{OPF_NS}}}item"):
        item_id = element.get("id")
        href = element.get("href")
        if item_id and href:
            manifest[item_id] = ManifestItem(
                id=item_id,
                href=href,
                media_type=element.get("media-type", ""),
                properties=element.get("properties"),
                element=element,
            )
    spine_ids = [
        element.get("idref")
        for element in root.iter(f"{{{OPF_NS}}}itemref")
        if element.get("idref")
    ]
    if not spine_ids:
        raise InputError(f"{path} has an empty spine")

    package = EpubPackage(
        source=path,
        files=files,
        order=order,
        opf_path=opf_path,
        opf_tree=opf_tree,
        version=version,
        manifest=manifest,
        spine_ids=spine_ids,
        docs=[],
    )
    _load_documents(package, archive)
    if not package.docs:
        raise InputError(f"{path} has no XHTML content documents in its spine")
    return package


def _load_documents(package: EpubPackage, archive: zipfile.ZipFile) -> None:
    for item_id in package.spine_ids:
        item = package.manifest.get(item_id)
        if item is None or item.media_type != "application/xhtml+xml":
            continue
        zip_path = package.resolve(item.href)
        if zip_path not in package.files:
            continue
        data = archive.read(zip_path)
        package.files[zip_path] = data
        tree, recovered, encoding = parse_content_document(data, zip_path)
        doc_index = len(package.docs)
        doc = ContentDoc(
            manifest_id=item_id,
            zip_path=zip_path,
            href=item.href,
            tree=tree,
            recovered=recovered,
            reencoded_from=encoding,
        )
        doc.sentences = annotate_document(doc.tree, doc_index)
        package.docs.append(doc)


# --- sentence extraction and id assignment ---------------------------------------------------


@dataclass
class _Item:
    """One piece of a block's flat content: its own text, a child element, or a child's tail."""

    kind: str
    node: etree._Element
    text: str
    start: int

    @property
    def end(self) -> int:
        return self.start + len(self.text)


def _collect_items(block: etree._Element) -> list[_Item]:
    items: list[_Item] = []
    position = 0
    if block.text:
        items.append(_Item("text", block, block.text, position))
        position += len(block.text)
    for child in block:
        if not isinstance(child.tag, str):
            if child.tail:
                items.append(_Item("text", child, child.tail, position))
                position += len(child.tail)
            continue
        skip = local_name(child.tag) in _SKIP_TAGS or _is_marker(child)
        text = "" if skip else "".join(child.itertext())
        items.append(_Item("elem", child, text, position))
        position += len(text)
        if child.tail:
            items.append(_Item("text", child, child.tail, position))
            position += len(child.tail)
    return items


def _iter_text_blocks(element: etree._Element):
    """Yield the deepest elements that hold running text, skipping non-readable subtrees."""
    tag = local_name(element.tag)
    if tag in _SKIP_TAGS or tag in _ATOMIC_TAGS:
        return
    block_children = [
        child
        for child in element
        if isinstance(child.tag, str) and local_name(child.tag) in _BLOCK_TAGS
    ]
    if block_children:
        for child in block_children:
            yield from _iter_text_blocks(child)
        return
    if "".join(element.itertext()).strip():
        yield element


def _snap_to_elements(boundaries: list[int], items: list[_Item]) -> list[int]:
    """Move split points that land inside a child element out to that element's edge.

    A sentence starting or ending inside ``<i>…</i>`` cannot be wrapped in a single span without
    breaking the markup, so it merges with its neighbour instead.
    """
    snapped: list[int] = []
    for boundary in boundaries:
        for item in items:
            if item.kind == "elem" and item.start < boundary < item.end:
                boundary = item.end
                break
        if not snapped or boundary > snapped[-1]:
            snapped.append(boundary)
    return snapped


def element_ids(tree: etree._ElementTree) -> set[str]:
    """Every id already in a content document, so generated ones never collide with them."""
    return {
        element.get("id")
        for element in tree.getroot().iter()
        if isinstance(element.tag, str) and element.get("id")
    }


def _unique_id(candidate: str, taken: set[str]) -> str:
    identifier = _ID_SAFE.sub("-", candidate)
    if identifier in taken:
        suffix = 2
        while f"{identifier}-{suffix}" in taken:
            suffix += 1
        identifier = f"{identifier}-{suffix}"
    taken.add(identifier)
    return identifier


def _slice_items(items: list[_Item], start: int, end: int) -> list[str | etree._Element]:
    """The content of character range ``[start, end)`` as text fragments and whole elements."""
    pieces: list[str | etree._Element] = []
    for item in items:
        if item.start == item.end:
            # An element contributing no text (a note marker, a page break) has no range to
            # overlap, so it is placed by position instead. Without this it falls between two
            # slices and is dropped.
            if item.kind == "elem" and start <= item.start < end:
                pieces.append(item.node)
            continue
        if item.end <= start or item.start >= end:
            continue
        if item.kind == "elem":
            pieces.append(item.node)
        else:
            pieces.append(
                item.text[max(0, start - item.start): min(len(item.text), end - item.start)]
            )
    return pieces


def _fill(container: etree._Element, pieces: list[str | etree._Element]) -> None:
    container.text = None
    for child in list(container):
        container.remove(child)
    for piece in pieces:
        if isinstance(piece, str):
            if not piece:
                continue
            if len(container):
                container[-1].tail = (container[-1].tail or "") + piece
            else:
                container.text = (container.text or "") + piece
        else:
            piece.tail = None
            container.append(piece)


def _structure_type(element: etree._Element) -> str | None:
    """The epub:type for the seq mirroring this element, or None if it needs no seq of its own."""
    for value in (element.get(f"{{{EPUB_NS}}}type") or "").split():
        if value in SKIPPABLE_TYPES:
            return value
    return ESCAPABLE_TAGS.get(local_name(element.tag))


def _structure_chain(
    block: etree._Element, body: etree._Element, label: int, taken: set[str], groups: int
) -> tuple[tuple[Structure, ...], int]:
    """Structures enclosing one block, outermost first, giving each an id where it has none."""
    chain: list[Structure] = []
    node: etree._Element | None = block
    while node is not None and node is not body and isinstance(node.tag, str):
        epub_type = _structure_type(node)
        if epub_type is not None:
            identifier = node.get("id")
            if not identifier:
                groups += 1
                identifier = _unique_id(f"ra-{label}-group-{groups}", taken)
                node.set("id", identifier)
            chain.append(Structure(epub_type, identifier))
        node = node.getparent()
    chain.reverse()
    return tuple(chain), groups


def _rewrite_block(
    block: etree._Element,
    items: list[_Item],
    units: list[tuple[int, int]],
    label: int,
    counter: int,
    taken: set[str],
) -> tuple[list[tuple[str, str]], int]:
    """Give every unit in ``block`` an id, wrapping in spans only where one is needed."""
    total = items[-1].end if items else 0
    joined = "".join(item.text for item in items)
    results: list[tuple[str, str]] = []
    if len(units) == 1 and not joined[: units[0][0]].strip() and not joined[units[0][1]:].strip():
        identifier = block.get("id")
        if not identifier:
            counter += 1
            identifier = _unique_id(f"ra-{label}-{counter}", taken)
            block.set("id", identifier)
        return [(identifier, joined[units[0][0]: units[0][1]])], counter

    # Detaching children first means moving them into spans cannot disturb the sibling walk.
    children = list(block)
    for child in children:
        block.remove(child)

    sequence: list[str | etree._Element] = []
    cursor = 0
    namespace = _namespace_of(block)
    for start, end in units:
        sequence.extend(_slice_items(items, cursor, start))
        pieces = _slice_items(items, start, end)
        single = pieces[0] if len(pieces) == 1 else None
        if single is not None and not isinstance(single, str) and single.get("id"):
            identifier = single.get("id")
            single.tail = None
            sequence.append(single)
        else:
            counter += 1
            identifier = _unique_id(f"ra-{label}-{counter}", taken)
            span = etree.Element(f"{namespace}span")
            span.set("id", identifier)
            _fill(span, pieces)
            sequence.append(span)
        results.append((identifier, joined[start:end]))
        cursor = end
    # total + 1 so a zero-length element sitting at the very end of the block is kept.
    sequence.extend(_slice_items(items, cursor, total + 1))
    _fill(block, sequence)
    return results, counter


def annotate_document(tree: etree._ElementTree, doc_index: int) -> list[Sentence]:
    """Split a content document into sentences and ensure each one has a fragment id.

    ``doc_index`` is the document's position in the spine, counted from zero, and is what
    ``package.docs`` is indexed by. Generated ids count from one, so the first document's
    sentences read ``ra-1-1`` rather than ``ra-0-1``.
    """
    label = doc_index + 1
    taken = element_ids(tree)
    root = tree.getroot()
    body = next((element for element in root.iter() if local_name(element.tag) == "body"), root)
    sentences: list[Sentence] = []
    counter = 0
    groups = 0
    for block in list(_iter_text_blocks(body)):
        items = _collect_items(block)
        joined = "".join(item.text for item in items)
        ranges = split_sentences(joined)
        if not ranges:
            continue
        boundaries = _snap_to_elements([end for _, end in ranges[:-1]], items)
        units: list[tuple[int, int]] = []
        cursor = ranges[0][0]
        for boundary in [*boundaries, ranges[-1][1]]:
            if boundary <= cursor:
                continue
            # Keep the whitespace between sentences outside the spans so a highlight starts
            # on the first letter rather than on the gap after the previous sentence.
            start = cursor
            while start < boundary and joined[start].isspace():
                start += 1
            end = boundary
            while end > start and joined[end - 1].isspace():
                end -= 1
            if end > start:
                units.append((start, end))
            cursor = boundary
        if not units:
            continue
        structure, groups = _structure_chain(block, body, label, taken, groups)
        produced, counter = _rewrite_block(block, items, units, label, counter, taken)
        for identifier, text in produced:
            words = text.split()
            if not words:
                continue
            sentences.append(
                Sentence(
                    doc_index=doc_index,
                    index=len(sentences),
                    fragment_id=identifier,
                    text=" ".join(words),
                    words=words,
                    structure=structure,
                )
            )
    return sentences
