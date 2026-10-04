"""Rewrite the package document and write the finished EPUB.

The OPF gains the audio and overlay manifest items, the ``media-overlay`` attribute on every
narrated content document, and the ``media:`` metadata EPUB 3.3 section 9.3.5 requires. The
archive is written mimetype-first and uncompressed, as OCF demands.
"""

from __future__ import annotations

import os
import posixpath
import re
import shutil
import zipfile
from urllib.parse import unquote

from lxml import etree

from .align import TimedSentence
from .audio import AudioTrack
from .clock import format_clock
from .epub import OPF_NS, EpubPackage, ManifestItem, local_name
from .errors import InputError
from .fs import ensure_directory
from .members import Member, MemberReader, SourceFile
from .smil import build_smil, overlay_duration

AUDIO_DIR = "audio"
OVERLAY_DIR = "media-overlays"

ACTIVE_CLASS = "-epub-media-overlay-active"
PLAYBACK_ACTIVE_CLASS = "-epub-media-overlay-playback-active"

#: Stylesheet injected so the highlight is visible in readers that honour media:active-class.
ACTIVE_CLASS_CSS = f""".{ACTIVE_CLASS} {{
\tbackground-color: rgba(255, 224, 96, 0.55);
\tcolor: inherit;
}}
"""

_MEDIA_TYPES_BY_SUFFIX = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".opus": "audio/ogg; codecs=opus",
}

#: The member names build_output gives its audio, e.g. ``audio/part007.m4a``. Matching this
#: pattern is what lets a re-run recognise the files a previous run embedded.
_AUDIO_NAME_RE = re.compile(
    r"audio/part\d{3}\.(?:" + "|".join(s.lstrip(".") for s in _MEDIA_TYPES_BY_SUFFIX) + ")"
)


def _manifest_element(package: EpubPackage) -> etree._Element:
    root = package.opf_tree.getroot()
    manifest = root.find(f"{{{OPF_NS}}}manifest")
    if manifest is None:
        raise InputError("the package document has no <manifest>")
    return manifest


def _metadata_element(package: EpubPackage) -> etree._Element:
    root = package.opf_tree.getroot()
    metadata = root.find(f"{{{OPF_NS}}}metadata")
    if metadata is None:
        raise InputError("the package document has no <metadata>")
    return metadata


def _free_id(package: EpubPackage, base: str) -> str:
    if base not in package.manifest:
        return base
    suffix = 2
    while f"{base}-{suffix}" in package.manifest:
        suffix += 1
    return f"{base}-{suffix}"


def _add_manifest_item(
    package: EpubPackage, manifest: etree._Element, item_id: str, href: str, media_type: str
) -> etree._Element:
    element = etree.SubElement(manifest, f"{{{OPF_NS}}}item")
    element.set("id", item_id)
    element.set("href", href)
    element.set("media-type", media_type)
    # Registered in package.manifest as well, or _free_id cannot see ids this build added and
    # hands the same id out twice.
    package.manifest[item_id] = ManifestItem(
        id=item_id, href=href, media_type=media_type, properties=None, element=element
    )
    return element


def _is_previous_audio(package: EpubPackage, zip_path: str) -> bool:
    """Whether an archive member is audio a previous readalign run embedded."""
    prefix = f"{package.opf_dir}/" if package.opf_dir else ""
    if not zip_path.startswith(prefix):
        return False
    return _AUDIO_NAME_RE.fullmatch(zip_path[len(prefix):]) is not None


def _strip_previous_run(package: EpubPackage, manifest: etree._Element) -> set[str]:
    """Drop a previous run's overlays and audio, so a re-run replaces rather than stacks."""
    removed: set[str] = set()
    for item in list(package.manifest.values()):
        if item.media_type == "application/smil+xml" or _is_previous_audio(
            package, package.resolve(item.href)
        ):
            manifest.remove(item.element)
            package.manifest.pop(item.id, None)
            removed.add(package.resolve(item.href))
        elif item.element.get("media-overlay"):
            del item.element.attrib["media-overlay"]
    return removed


def _drop_existing_overlay_metadata(metadata: etree._Element) -> None:
    for element in list(metadata):
        if element.tag == f"{{{OPF_NS}}}meta" and (element.get("property") or "").startswith(
            "media:"
        ):
            metadata.remove(element)


def _set_meta(metadata: etree._Element, prop: str, value: str, refines: str | None = None) -> None:
    element = etree.SubElement(metadata, f"{{{OPF_NS}}}meta")
    element.set("property", prop)
    if refines:
        element.set("refines", refines)
    element.text = value


def _links_to(doc, css_path: str) -> list[etree._Element]:
    """The <link> elements in one document that point at ``css_path``."""
    found = []
    for element in doc.tree.getroot().iter():
        if local_name(element.tag) != "link":
            continue
        href = element.get("href")
        if not href:
            continue
        resolved = posixpath.normpath(
            posixpath.join(posixpath.dirname(doc.zip_path), unquote(href))
        )
        if resolved == css_path:
            found.append(element)
    return found


def _strip_previous_stylesheet(
    package: EpubPackage, manifest: etree._Element, css_path: str
) -> None:
    """Drop an earlier run's stylesheet, so re-running replaces the link rather than stacking."""
    for item in list(package.manifest.values()):
        if item.media_type == "text/css" and package.resolve(item.href) == css_path:
            manifest.remove(item.element)
            package.manifest.pop(item.id, None)
    for doc in package.docs:
        for link in _links_to(doc, css_path):
            link.getparent().remove(link)


def _inject_stylesheet(
    package: EpubPackage, manifest: etree._Element, href: str, css_path: str
) -> None:
    _strip_previous_stylesheet(package, manifest, css_path)
    _add_manifest_item(package, manifest, _free_id(package, "readalign-css"), href, "text/css")
    for doc in package.docs:
        root = doc.tree.getroot()
        head = next(
            (element for element in root.iter() if local_name(element.tag) == "head"),
            None,
        )
        if head is None:
            continue
        namespace = root.tag[: root.tag.index("}") + 1] if root.tag.startswith("{") else ""
        link = etree.SubElement(head, f"{namespace}link")
        link.set("rel", "stylesheet")
        link.set("type", "text/css")
        link.set("href", posixpath.relpath(css_path, posixpath.dirname(doc.zip_path)))


def build_output(
    package: EpubPackage,
    entries_by_doc: dict[str, list[TimedSentence]],
    tracks: list[AudioTrack],
    *,
    narrator: str | None = None,
) -> tuple[Member, dict]:
    """Produce the member-name-to-bytes map of the output EPUB, and a summary of what changed."""
    manifest = _manifest_element(package)
    metadata = _metadata_element(package)
    _drop_existing_overlay_metadata(metadata)
    previous = _strip_previous_run(package, manifest)

    by_index = {track.index: track for track in tracks}
    used_tracks = sorted(
        {entry.track_index for entries in entries_by_doc.values() for entry in entries}
    )
    audio_paths: dict[int, str] = {}
    audio_hrefs: dict[int, str] = {}
    # A previous run's audio is dropped even without a manifest item, so nothing dead rides
    # along when this run embeds different files.
    files: Member = {
        name: data
        for name, data in package.files.items()
        if name not in previous and not _is_previous_audio(package, name)
    }

    for index in used_tracks:
        track = by_index[index]
        suffix = os.path.splitext(track.path)[1].lower()
        href = posixpath.join(AUDIO_DIR, f"part{index:03d}{suffix}")
        zip_path = package.resolve(href)
        audio_paths[index] = zip_path
        audio_hrefs[index] = href
        files[zip_path] = SourceFile(track.path)
        _add_manifest_item(
            package,
            manifest,
            _free_id(package, f"readalign-audio-{index:03d}"),
            href,
            _MEDIA_TYPES_BY_SUFFIX.get(suffix, track.media_type),
        )

    css_href = posixpath.join("css", "readalign.css")
    css_path = package.resolve(css_href)
    files[css_path] = ACTIVE_CLASS_CSS.encode("utf-8")
    _inject_stylesheet(package, manifest, css_href, css_path)

    overlays: list[tuple[str, float]] = []
    total = 0.0
    for doc in package.docs:
        entries = entries_by_doc.get(doc.manifest_id) or []
        if not entries:
            continue
        stem = posixpath.splitext(posixpath.basename(doc.href))[0]
        href = posixpath.join(OVERLAY_DIR, f"{stem}.smil")
        smil_path = package.resolve(href)
        files[smil_path] = build_smil(smil_path, doc.zip_path, entries, by_index, audio_paths)
        overlay_id = _free_id(package, f"readalign-smil-{doc.manifest_id}")
        _add_manifest_item(package, manifest, overlay_id, href, "application/smil+xml")
        package.manifest[doc.manifest_id].element.set("media-overlay", overlay_id)
        duration = overlay_duration(entries)
        overlays.append((overlay_id, duration))
        total += duration

    for overlay_id, duration in overlays:
        _set_meta(metadata, "media:duration", format_clock(duration), refines=f"#{overlay_id}")
    _set_meta(metadata, "media:duration", format_clock(total))
    _set_meta(metadata, "media:active-class", ACTIVE_CLASS)
    _set_meta(metadata, "media:playback-active-class", PLAYBACK_ACTIVE_CLASS)
    if narrator:
        _set_meta(metadata, "media:narrator", narrator)

    for doc in package.docs:
        files[doc.zip_path] = doc.serialize()
    files[package.opf_path] = etree.tostring(
        package.opf_tree, xml_declaration=True, encoding="utf-8"
    )

    summary = {
        "overlays": len(overlays),
        "total_duration": format_clock(total),
        "audio_files": len(audio_paths),
        "audio_hrefs": audio_hrefs,
        "active_class": ACTIVE_CLASS,
    }
    return files, summary


def write_epub(path: str, files: Member, original_order: list[str]) -> None:
    """Write an OCF archive: mimetype first and stored, everything else deflated."""
    ordered: list[str] = []
    seen = set()
    for name in ["mimetype", "META-INF/container.xml", *original_order]:
        if name in files and name not in seen:
            ordered.append(name)
            seen.add(name)
    ordered.extend(sorted(name for name in files if name not in seen))

    ensure_directory(os.path.dirname(os.path.abspath(path)))

    try:
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive, MemberReader() as read:
            for name in ordered:
                data = files[name]
                if name == "mimetype":
                    # OCF requires the first entry to be an uncompressed "mimetype".
                    info = zipfile.ZipInfo("mimetype")
                    info.compress_type = zipfile.ZIP_STORED
                    info.external_attr = 0o644 << 16
                    archive.writestr(info, data)
                    continue
                info = zipfile.ZipInfo(name)
                info.external_attr = 0o644 << 16
                info.compress_type = (
                    zipfile.ZIP_STORED
                    if posixpath.splitext(name)[1].lower() in _MEDIA_TYPES_BY_SUFFIX
                    else zipfile.ZIP_DEFLATED
                )
                if isinstance(data, bytes):
                    archive.writestr(info, data)
                else:
                    with read.open(data) as source, archive.open(info, "w") as target:
                        shutil.copyfileobj(source, target, 1 << 20)
    except OSError as exc:
        raise InputError(f"cannot write {path}: {exc}") from exc


def ensure_mimetype(files: Member) -> None:
    """Set the OCF mimetype. It is fixed for EPUB, so the input's copy is never carried over."""
    files["mimetype"] = b"application/epub+zip"
