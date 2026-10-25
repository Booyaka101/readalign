"""The shipped artefact: SMIL structure, the rewritten OPF, and the OCF archive itself."""

import os
import shutil
import subprocess
import zipfile
from urllib.parse import quote

import pytest
from lxml import etree

from conftest import CHAPTERS, STRUCTURED, build_epub, make_tracks, speak
from readalign.align import align
from readalign.cli import check_epub
from readalign.epub import OPF_NS, load_epub, local_name
from readalign.members import ArchiveMember, SourceFile
from readalign.package import ACTIVE_CLASS, build_output, ensure_mimetype, write_epub
from readalign.smil import EPUB_NS, SMIL_NS

has_ffmpeg = shutil.which("ffmpeg") and shutil.which("ffprobe")


def silent_mp3(path, seconds):
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
         "-i", "anullsrc=r=22050:cl=mono", "-t", str(seconds), "-c:a", "libmp3lame", str(path)],
        check=True,
    )
    return str(path)


def entries_for(package, tracks):
    """Time a package against synthetic narration and group the result the way a build does."""
    sentences = [sentence for doc in package.docs for sentence in doc.sentences]
    result = align(sentences, speak(sentences), tracks, log=lambda *_: None)
    entries = {}
    for entry in result.timed:
        entries.setdefault(package.docs[entry.sentence.doc_index].manifest_id, []).append(entry)
    return result, entries


def timed_epub(tmp_path, chapters=CHAPTERS):
    """A real EPUB plus a real (silent) MP3, timed with a synthetic transcript."""
    package = load_epub(build_epub(str(tmp_path / "in.epub"), chapters=chapters))
    words = speak([sentence for doc in package.docs for sentence in doc.sentences])
    duration = words[-1].end + 2.0
    tracks = make_tracks([duration])
    if has_ffmpeg:
        tracks[0].path = silent_mp3(tmp_path / "part001.mp3", duration)
    result, entries = entries_for(package, tracks)
    return package, result, tracks, entries


@pytest.fixture
def aligned(tmp_path):
    return timed_epub(tmp_path)


def test_smil_documents_are_well_formed(aligned):
    package, _, tracks, entries = aligned
    if not has_ffmpeg:
        pytest.skip("ffmpeg is needed to embed real audio")
    files, summary = build_output(package, entries, tracks)
    smils = [name for name in files if name.endswith(".smil")]
    assert len(smils) == summary["overlays"] == 2
    root = etree.fromstring(files[smils[0]])
    assert root.tag == f"{{{SMIL_NS}}}smil"
    assert root.get("version") == "3.0"
    body = root.find(f"{{{SMIL_NS}}}body")
    assert body.get("{http://www.idpf.org/2007/ops}textref").endswith("chapter1.xhtml")
    par = body.find(f"{{{SMIL_NS}}}par")
    assert par.find(f"{{{SMIL_NS}}}text").get("src").endswith("#ra-1-1")
    assert par.find(f"{{{SMIL_NS}}}audio").get("clipBegin").count(":") == 2


def test_skippable_content_is_wrapped_in_a_seq(aligned):
    package, _, tracks, entries = aligned
    if not has_ffmpeg:
        pytest.skip("ffmpeg is needed to embed real audio")
    files, _ = build_output(package, entries, tracks)
    chapter2 = next(name for name in files if name.endswith("chapter2.smil"))
    root = etree.fromstring(files[chapter2])
    seqs = root.findall(f".//{{{SMIL_NS}}}seq")
    assert len(seqs) == 1
    assert seqs[0].get("{http://www.idpf.org/2007/ops}type") == "footnote"
    assert seqs[0].get("{http://www.idpf.org/2007/ops}textref").endswith("#fn1")


def test_package_document_carries_the_overlay_metadata(aligned):
    package, _, tracks, entries = aligned
    if not has_ffmpeg:
        pytest.skip("ffmpeg is needed to embed real audio")
    files, _ = build_output(package, entries, tracks, narrator="A Narrator")
    opf = etree.fromstring(files[package.opf_path])
    items = {item.get("id"): item for item in opf.iter(f"{{{OPF_NS}}}item")}
    assert items["c1"].get("media-overlay") in items
    assert items[items["c1"].get("media-overlay")].get("media-type") == "application/smil+xml"
    metas = [
        (meta.get("property"), meta.get("refines"), meta.text)
        for meta in opf.iter(f"{{{OPF_NS}}}meta")
    ]
    durations = [meta for meta in metas if meta[0] == "media:duration"]
    assert len(durations) == 3  # one per overlay plus the unrefined total
    assert sum(1 for meta in durations if meta[1] is None) == 1
    assert ("media:active-class", None, ACTIVE_CLASS) in metas
    assert ("media:narrator", None, "A Narrator") in metas


def test_archive_starts_with_a_stored_mimetype(aligned, tmp_path):
    package, _, tracks, entries = aligned
    if not has_ffmpeg:
        pytest.skip("ffmpeg is needed to embed real audio")
    files, _ = build_output(package, entries, tracks)
    ensure_mimetype(files)
    out = str(tmp_path / "out.epub")
    write_epub(out, files, package.order)
    with zipfile.ZipFile(out) as archive:
        first = archive.infolist()[0]
        assert first.filename == "mimetype"
        assert first.compress_type == zipfile.ZIP_STORED
        assert archive.read("mimetype") == b"application/epub+zip"


@pytest.mark.skipif(not has_ffmpeg, reason="ffmpeg and ffprobe are needed")
def test_rebuilding_an_aligned_epub_replaces_rather_than_stacks(aligned, tmp_path):
    """Re-running build over its own output must not accumulate overlays or stylesheet links."""
    package, _, tracks, entries = aligned
    files, first = build_output(package, entries, tracks)
    ensure_mimetype(files)
    out = str(tmp_path / "again.epub")
    write_epub(out, files, package.order)

    for _ in range(2):
        reloaded = load_epub(out)
        files, second = build_output(reloaded, entries_for(reloaded, tracks)[1], tracks)
        ensure_mimetype(files)
        write_epub(out, files, reloaded.order)

    final = load_epub(out)
    opf = etree.fromstring(files[final.opf_path])
    items = list(opf.iter(f"{{{OPF_NS}}}item"))
    assert second["overlays"] == first["overlays"]
    assert sum(1 for item in items if item.get("media-type") == "text/css") == 1
    assert sum(1 for item in items if item.get("media-type") == "application/smil+xml") == 2
    audio = [
        (item.get("id"), item.get("href"))
        for item in items
        if (item.get("media-type") or "").startswith("audio/")
    ]
    assert audio == [("readalign-audio-001", "audio/part001.mp3")]
    for doc in final.docs:
        links = [element for element in doc.tree.getroot().iter()
                 if local_name(element.tag) == "link"]
        assert len(links) == 1, f"{doc.href} has {len(links)} stylesheet links"
    assert check_epub(out)["errors"] == []


def test_rebuilding_with_different_audio_drops_the_old_file(aligned, tmp_path):
    """A re-run whose prepared audio gets a new name must not ship the old file as dead weight."""
    package, _, tracks, entries = aligned
    files, _ = build_output(package, entries, tracks)
    files = {
        name: (b"audio" if isinstance(data, SourceFile) else data)
        for name, data in files.items()
    }
    ensure_mimetype(files)
    out = str(tmp_path / "again.epub")
    write_epub(out, files, package.order)

    reloaded = load_epub(out)
    replacement = make_tracks([tracks[0].duration], suffix=".m4a")
    _, fresh = entries_for(reloaded, replacement)
    files, _ = build_output(reloaded, fresh, replacement)

    assert sorted(name for name in files if "/audio/" in name) == ["EPUB/audio/part001.m4a"]
    opf = etree.fromstring(files[reloaded.opf_path])
    audio = [
        (item.get("id"), item.get("href"))
        for item in opf.iter(f"{{{OPF_NS}}}item")
        if (item.get("media-type") or "").startswith("audio/")
    ]
    assert audio == [("readalign-audio-001", "audio/part001.m4a")]


def test_building_twice_over_one_package_keeps_ids_unique(aligned):
    """Items added in one build must be visible to the next, or ids repeat."""
    package, _, tracks, entries = aligned
    build_output(package, entries, tracks)
    files, _ = build_output(package, entries, tracks)
    opf = etree.fromstring(files[package.opf_path])
    ids = [item.get("id") for item in opf.iter(f"{{{OPF_NS}}}item")]
    assert len(ids) == len(set(ids))
    audio = [
        item.get("href")
        for item in opf.iter(f"{{{OPF_NS}}}item")
        if (item.get("media-type") or "").startswith("audio/")
    ]
    assert audio == ["audio/part001.mp3"]


@pytest.mark.skipif(not has_ffmpeg, reason="ffmpeg and ffprobe are needed")
def test_check_accepts_what_build_wrote(aligned, tmp_path):
    package, result, tracks, entries = aligned
    files, _ = build_output(package, entries, tracks)
    ensure_mimetype(files)
    out = str(tmp_path / "checked.epub")
    write_epub(out, files, package.order)
    findings = check_epub(out)
    assert findings["errors"] == []
    assert findings["ok"]
    assert findings["sentences"] == len(result.timed)


@pytest.mark.parametrize("from_archive", [False, True])
def test_streamed_audio_selects_zip64_before_writing(tmp_path, monkeypatch, from_archive):
    """Lower the threshold to exercise multi-gigabyte audio without a huge fixture."""
    payload = b"a" * 4096
    if from_archive:
        original = tmp_path / "original.epub"
        with zipfile.ZipFile(original, "w") as archive:
            archive.writestr("audio.mp3", payload)
        member = ArchiveMember(str(original), "audio.mp3")
    else:
        original = tmp_path / "audio.mp3"
        original.write_bytes(payload)
        member = SourceFile(str(original))
    monkeypatch.setattr(zipfile, "ZIP64_LIMIT", 1024)
    out = tmp_path / "large.epub"
    small = tmp_path / "small.mp3"
    small.write_bytes(b"small")
    files = {"audio.mp3": member, "small.mp3": SourceFile(str(small))}
    ensure_mimetype(files)
    write_epub(str(out), files, ["small.mp3"])
    with zipfile.ZipFile(out) as archive:
        assert archive.read("audio.mp3") == payload
        assert archive.getinfo("audio.mp3").extract_version == 45
        assert archive.getinfo("small.mp3").extract_version == 20


@pytest.mark.parametrize("shared_track", [True, False])
def test_long_book_uses_file_local_clocks_and_stored_audio(tmp_path, shared_track):
    from readalign.align import TimedSentence
    from readalign.clock import parse_clock
    from readalign.sidecar import build_vtt

    chapters = [(f"Chapter {index}", "".join(
        f"<p>Voyage sentence {n} describes the sea.</p>" for n in range(450)
    )) for index in range(2)]
    package = load_epub(build_epub(str(tmp_path / "long.epub"), chapters=chapters))
    tracks = make_tracks([16 * 3600] if shared_track else [8 * 3600, 8 * 3600])
    # These bytes exercise packaging only, not audio decoding or reader playback.
    for track in tracks:
        path = tmp_path / f"track{track.index}.mp3"
        path.write_bytes(b"synthetic audio")
        track.path = str(path)
    by_doc = {}
    for index, doc in enumerate(package.docs):
        start = index * 8 * 3600 + 15.125
        by_doc[doc.manifest_id] = [
            TimedSentence(sentence, start + n * 2, start + n * 2 + 1.5,
                          confidence=1.0, track_index=1 if shared_track else index + 1)
            for n, sentence in enumerate(doc.sentences)
        ]
    files, summary = build_output(package, by_doc, tracks)
    assert summary["audio_files"] == len(tracks)
    out = str(tmp_path / "out.epub")
    ensure_mimetype(files)
    write_epub(out, files, package.order)
    assert check_epub(out, probe_audio=False)["ok"]
    with zipfile.ZipFile(out) as archive:
        for index, doc in enumerate(package.docs):
            root = etree.fromstring(archive.read(f"EPUB/media-overlays/chapter{index + 1}.smil"))
            clips = list(root.iter(f"{{{SMIL_NS}}}audio"))
            expected_start = index * 8 * 3600 + 15.125 if shared_track else 15.125
            assert parse_clock(clips[0].get("clipBegin")) == expected_start
            assert len(clips) == len(doc.sentences) == 450
            track = tracks[0 if shared_track else index]
            vtt = build_vtt(by_doc[doc.manifest_id], track, track.href)
            assert ("08:00:15.125" if shared_track and index else "00:00:15.125") in vtt
        for info in archive.infolist():
            if info.filename.endswith(".mp3"):
                assert info.compress_type == zipfile.ZIP_STORED
                assert archive.read(info) == b"synthetic audio"


def _clip_past_the_end(root):
    root.find(f".//{{{SMIL_NS}}}audio").set("clipEnd", "9:59:59.000")


def _dangling_fragment(root):
    text = root.find(f".//{{{SMIL_NS}}}text")
    text.set("src", text.get("src").split("#")[0] + "#ra-does-not-exist")


@pytest.mark.skipif(not has_ffmpeg, reason="ffmpeg and ffprobe are needed")
@pytest.mark.parametrize(
    ("break_it", "expected"),
    [
        (_clip_past_the_end, "past the file's real duration"),
        (_dangling_fragment, "ra-does-not-exist"),
    ],
)
def test_check_reports_a_broken_overlay(aligned, tmp_path, break_it, expected):
    package, _, tracks, entries = aligned
    files, _ = build_output(package, entries, tracks)
    ensure_mimetype(files)
    smil_name = next(name for name in files if name.endswith(".smil"))
    root = etree.fromstring(files[smil_name])
    break_it(root)
    files[smil_name] = etree.tostring(root, xml_declaration=True, encoding="utf-8")
    out = str(tmp_path / "broken.epub")
    write_epub(out, files, package.order)
    findings = check_epub(out)
    assert not findings["ok"]
    assert any(expected in error for error in findings["errors"])


def test_check_refuses_something_that_is_not_an_epub(tmp_path):
    from readalign.errors import InputError

    path = tmp_path / "plain.epub"
    path.write_bytes(b"not a zip at all")
    with pytest.raises(InputError):
        check_epub(str(path))


@pytest.mark.skipif(
    not os.environ.get("READALIGN_MO_TESTBOOK"),
    reason="set READALIGN_MO_TESTBOOK to the epubtest.org Media Overlays test book",
)
def test_checker_accepts_the_official_media_overlays_test_book():
    """Guards the checker itself: it must pass a book someone else built to the spec."""
    findings = check_epub(os.environ["READALIGN_MO_TESTBOOK"])
    assert findings["errors"] == []
    assert findings["overlays"] > 0


def overlay_shape(element):
    """A seq/par tree as nested tuples, so a test can state the nesting it expects."""
    shape = []
    for child in element:
        if local_name(child.tag) == "seq":
            shape.append((child.get(f"{{{EPUB_NS}}}type"), overlay_shape(child)))
        else:
            shape.append(child.find(f"{{{SMIL_NS}}}text").get("src").rsplit("#", 1)[1])
    return shape


@pytest.mark.skipif(not has_ffmpeg, reason="ffmpeg and ffprobe are needed")
def test_overlay_nesting_mirrors_the_document(tmp_path):
    """Escapability: a reading system can only leave a table if the seqs mirror the markup."""
    package, _, tracks, entries = timed_epub(tmp_path, chapters=[("One", STRUCTURED)])
    files, _ = build_output(package, entries, tracks)
    smil = next(name for name in files if name.endswith(".smil"))
    body = etree.fromstring(files[smil]).find(f"{{{SMIL_NS}}}body")
    types = [level for level in overlay_shape(body) if isinstance(level, tuple)]
    assert [name for name, _ in types] == ["table", "list", "figure"]

    table = dict(types)["table"]
    assert [name for name, _ in table] == ["table-row"]
    assert [name for name, _ in table[0][1]] == ["table-cell", "table-cell"]

    def seqs(shape):
        return [item for item in shape if isinstance(item, tuple)]

    nested = dict(types)["list"]
    assert [name for name, _ in nested] == ["list-item", "list-item"]
    # The second item holds a paragraph of its own and then a sublist, in that order.
    assert isinstance(nested[1][1][0], str)
    assert [name for name, _ in seqs(nested[1][1])] == ["list"]


@pytest.mark.skipif(not has_ffmpeg, reason="ffmpeg and ffprobe are needed")
def test_every_sentence_still_gets_exactly_one_par(tmp_path):
    """Nesting must not drop or repeat a sentence, whatever structure it sits in."""
    package, result, tracks, entries = timed_epub(tmp_path, chapters=[("One", STRUCTURED)])
    files, _ = build_output(package, entries, tracks)
    smil = next(name for name in files if name.endswith(".smil"))
    root = etree.fromstring(files[smil])
    srcs = [
        text.get("src").rsplit("#", 1)[1]
        for text in root.iter(f"{{{SMIL_NS}}}text")
    ]
    assert srcs == [entry.sentence.fragment_id for entry in result.timed]


@pytest.mark.skipif(not has_ffmpeg, reason="ffmpeg and ffprobe are needed")
def test_nested_chapters_with_reserved_url_characters_keep_distinct_overlays(aligned, tmp_path):
    package, _, tracks, entries = aligned
    for index, doc in enumerate(package.docs):
        package.files.pop(doc.zip_path)
        doc.zip_path = f"EPUB/volume{index}/Chapter #1%.xhtml"
        doc.href = quote(f"volume{index}/Chapter #1%.xhtml")
        item = package.manifest[doc.manifest_id]
        item.href = doc.href
        item.element.set("href", doc.href)
    files, summary = build_output(package, entries, tracks)
    smils = [name for name in files if name.endswith(".smil")]
    assert len(smils) == len(set(smils)) == summary["overlays"] == 2
    for name in smils:
        root = etree.fromstring(files[name])
        src = root.find(f".//{{{SMIL_NS}}}text").get("src")
        assert "%23" in src and "%25" in src and "%20" in src
    out = str(tmp_path / "nested.epub")
    ensure_mimetype(files)
    write_epub(out, files, package.order)
    assert check_epub(out)["ok"]


def _unmanifested_audio(files, package):
    opf = etree.fromstring(files[package.opf_path])
    for item in list(opf.iter(f"{{{OPF_NS}}}item")):
        if (item.get("media-type") or "").startswith("audio/"):
            item.getparent().remove(item)
    files[package.opf_path] = etree.tostring(opf)


def _wrong_total(files, package):
    opf = etree.fromstring(files[package.opf_path])
    for meta in opf.iter(f"{{{OPF_NS}}}meta"):
        if meta.get("property") == "media:duration" and not meta.get("refines"):
            meta.text = "99:00:00.000"
    files[package.opf_path] = etree.tostring(opf)


def _empty_smil(files, package):
    name = next(name for name in files if name.endswith(".smil"))
    files[name] = f'<smil xmlns="{SMIL_NS}" version="3.0"><body/></smil>'.encode()


def _foreign_document(files, package):
    name = next(name for name in files if name.endswith("chapter1.smil"))
    files[name] = files[name].replace(b"chapter1.xhtml", b"chapter2.xhtml")


@pytest.mark.skipif(not has_ffmpeg, reason="ffmpeg is needed")
@pytest.mark.parametrize("mutate,expected", [
    (_unmanifested_audio, "not a manifested audio"),
    (_wrong_total, "package media:duration declares"),
    (_empty_smil, "no SMIL par"),
    (_foreign_document, "outside its associated document"),
])
def test_checker_rejects_invalid_package_relationships(aligned, tmp_path, mutate, expected):
    package, _, tracks, entries = aligned
    files, _ = build_output(package, entries, tracks)
    mutate(files, package)
    out = str(tmp_path / "invalid.epub")
    ensure_mimetype(files)
    write_epub(out, files, package.order)
    findings = check_epub(out, probe_audio=False)
    assert not findings["ok"]
    assert any(expected in error for error in findings["errors"])


def test_audio_probe_never_uses_archive_names_as_os_paths(tmp_path, monkeypatch):
    from readalign import audio
    from readalign.cli import _probe_clip_ends

    archive_path = tmp_path / "hostile.epub"
    hostile_name = "D:escaped.mp3"
    with zipfile.ZipFile(archive_path, "w") as archive:
        info = zipfile.ZipInfo("placeholder")
        info.filename = hostile_name
        archive.writestr(info, b"audio")

    def probe(path):
        assert os.path.basename(path) == "audio-0000"
        assert os.path.isfile(path)
        return {"format": {"duration": "10"}, "streams": [{"codec_type": "audio"}]}

    monkeypatch.setattr(audio, "ffprobe", probe)
    with zipfile.ZipFile(archive_path) as archive:
        errors = []
        result = _probe_clip_ends(archive, {hostile_name: 9.0}, errors, [])
    assert result == {hostile_name: 10.0}
    assert not errors


def test_generated_resources_preserve_unrelated_existing_audio_and_css(aligned):
    from readalign.package import _add_manifest_item, _manifest_element

    package, _, tracks, entries = aligned
    manifest = _manifest_element(package)
    originals = {"EPUB/audio/part001.mp3": b"original embedded audio",
                 "EPUB/css/readalign.css": b"original ebook stylesheet"}
    package.files.update(originals)
    _add_manifest_item(package, manifest, "original-audio", "audio/part001.mp3", "audio/mpeg")
    _add_manifest_item(package, manifest, "original-css", "css/readalign.css", "text/css")
    files, summary = build_output(package, entries, tracks)
    assert all(files[name] == data for name, data in originals.items())
    assert summary["audio_hrefs"][1] == "audio/part001.2.mp3"
    assert "EPUB/css/readalign.2.css" in files
    assert {"original-audio", "original-css"} <= set(package.manifest)
