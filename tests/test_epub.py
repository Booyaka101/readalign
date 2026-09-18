"""Loading, sentence annotation and the refusals that must not turn into stack traces."""

import pytest
from lxml import etree

from conftest import DOC, STRUCTURED, build_epub
from readalign.cli import check_epub
from readalign.epub import element_ids, load_epub, local_name, parse_content_document
from readalign.errors import DRMError, InputError

ENCRYPTION = """<?xml version="1.0" encoding="UTF-8"?>
<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <EncryptedData xmlns="http://www.w3.org/2001/04/xmlenc#">
    <EncryptionMethod Algorithm="http://www.w3.org/2001/04/xmlenc#aes128-cbc"/>
  </EncryptedData>
</encryption>
"""

FONT_OBFUSCATION = """<?xml version="1.0" encoding="UTF-8"?>
<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <EncryptedData xmlns="http://www.w3.org/2001/04/xmlenc#">
    <EncryptionMethod Algorithm="http://www.idpf.org/2008/embedding"/>
  </EncryptedData>
</encryption>
"""


def sentences_of(package):
    return [sentence for doc in package.docs for sentence in doc.sentences]


def test_loads_and_annotates(sample_epub):
    package = load_epub(sample_epub)
    sentences = sentences_of(package)
    assert [sentence.fragment_id for sentence in sentences[:3]] == ["ra-1-1", "ra-1-2", "ra-1-3"]
    assert sentences[1].text == "The ship left the harbour at dawn."
    assert package.title() == "A Test Voyage"


def test_annotation_loses_no_text(sample_epub):
    package = load_epub(sample_epub)
    def flatten(tree):
        return " ".join("".join(tree.itertext()).split())

    before = {
        doc.zip_path: flatten(etree.fromstring(package.files[doc.zip_path]))
        for doc in package.docs
    }
    sentences_of(package)
    for doc in package.docs:
        assert flatten(doc.tree.getroot()) == before[doc.zip_path]


def test_ids_are_stable_across_runs(sample_epub):
    first = [sentence.fragment_id for sentence in sentences_of(load_epub(sample_epub))]
    second = [sentence.fragment_id for sentence in sentences_of(load_epub(sample_epub))]
    assert first == second


def test_existing_ids_are_never_overwritten(tmp_path):
    path = build_epub(
        str(tmp_path / "ids.epub"),
        chapters=[("One", '<p id="keep-me">A single sentence stands alone.</p>')],
    )
    sentences = sentences_of(load_epub(path))
    assert [sentence.fragment_id for sentence in sentences] == ["keep-me"]


def test_footnote_gets_its_epub_type(sample_epub):
    sentences = sentences_of(load_epub(sample_epub))
    footnotes = [sentence for sentence in sentences if sentence.structure]
    assert len(footnotes) == 1
    assert [(level.epub_type, level.container_id) for level in footnotes[0].structure] == [
        ("footnote", "fn1")
    ]


def test_noteref_marker_does_not_merge_sentences(sample_epub):
    texts = [sentence.text for sentence in sentences_of(load_epub(sample_epub))]
    assert "We sighted ice on the ninth day." in texts
    assert "The captain ordered the sails reefed." in texts


def test_document_lying_about_its_encoding_is_recovered(tmp_path):
    # A declaration saying UTF-8 over windows-1252 bytes is what actually turns up in the wild.
    body = "<p>The café was closed. He walked on.</p>"
    document = DOC.format(title="Latin", body=body)
    path = build_epub(
        str(tmp_path / "cp1252.epub"),
        chapters=[("One", "<p>placeholder</p>")],
        extra={"EPUB/chapter1.xhtml": document.encode("cp1252")},
    )
    package = load_epub(path)
    sentences = sentences_of(package)
    assert package.docs[0].reencoded_from == "cp1252"
    assert sentences[0].text == "The café was closed."


def test_declared_encoding_is_honoured(tmp_path):
    document = DOC.format(title="Latin", body="<p>The café was closed.</p>").replace(
        'encoding="UTF-8"', 'encoding="windows-1252"'
    )
    path = build_epub(
        str(tmp_path / "declared.epub"),
        chapters=[("One", "<p>placeholder</p>")],
        extra={"EPUB/chapter1.xhtml": document.encode("cp1252")},
    )
    assert sentences_of(load_epub(path))[0].text == "The café was closed."


def test_broken_xml_is_parsed_in_recovery_mode():
    tree, recovered, _ = parse_content_document(
        b"<html xmlns='http://www.w3.org/1999/xhtml'><body><p>Broken & wrong.</body></html>",
        "broken.xhtml",
    )
    assert recovered
    assert "Broken" in "".join(tree.getroot().itertext())


def test_drm_encrypted_epub_is_refused(tmp_path):
    path = build_epub(str(tmp_path / "drm.epub"), extra={"META-INF/encryption.xml": ENCRYPTION})
    with pytest.raises(DRMError) as info:
        load_epub(path)
    assert "DRM-protected" in str(info.value)
    assert info.value.exit_code == 2


def test_obfuscated_fonts_are_not_drm(tmp_path):
    path = build_epub(
        str(tmp_path / "fonts.epub"), extra={"META-INF/encryption.xml": FONT_OBFUSCATION}
    )
    assert load_epub(path).docs


def test_missing_file_is_a_clean_error(tmp_path):
    with pytest.raises(InputError) as info:
        load_epub(str(tmp_path / "nope.epub"))
    assert "nope.epub" in str(info.value)


def test_not_a_zip_is_a_clean_error(tmp_path):
    path = tmp_path / "text.epub"
    path.write_text("this is not an epub", encoding="utf-8")
    with pytest.raises(InputError):
        load_epub(str(path))


@pytest.mark.parametrize("entry_point", [load_epub, check_epub])
def test_a_directory_is_a_clean_error(tmp_path, entry_point):
    """An unzipped book is an easy mistake, and it must not reach the user as a traceback."""
    unzipped = tmp_path / "unzipped.epub"
    unzipped.mkdir()
    with pytest.raises(InputError) as info:
        entry_point(str(unzipped))
    assert "directory" in str(info.value)


def test_epub_2_is_refused_with_a_hint(tmp_path):
    path = build_epub(str(tmp_path / "old.epub"), version="2.0")
    with pytest.raises(InputError) as info:
        load_epub(path)
    assert "EPUB 3" in str(info.value)


def test_structure_chain_mirrors_the_document(tmp_path):
    path = build_epub(str(tmp_path / "structured.epub"), chapters=[("One", STRUCTURED)])
    chains = {
        sentence.text: [level.epub_type for level in sentence.structure]
        for sentence in sentences_of(load_epub(path))
    }
    assert chains["The ship left the harbour at dawn."] == []
    assert chains["The first cell holds a sentence."] == ["table", "table-row", "table-cell"]
    assert chains["A nested item goes here."] == ["list", "list-item", "list", "list-item"]
    assert chains["A caption for the plate."] == ["figure"]


def test_structures_are_given_ids_to_point_at(tmp_path):
    path = build_epub(str(tmp_path / "ids.epub"), chapters=[("One", STRUCTURED)])
    package = load_epub(path)
    ids = element_ids(package.docs[0].tree)
    for sentence in sentences_of(package):
        for level in sentence.structure:
            assert level.container_id in ids


LOOSE = (
    "<ul><li>The second item carries more weight."
    "<ol><li>A nested item goes here.</li></ol></li></ul>"
    "<blockquote><p>He walked on without looking back.</p>"
    "<cite>Some Book, page four.</cite></blockquote>"
)


def test_text_beside_a_block_child_is_still_narrated(tmp_path):
    """Text in <li>one<ol>…</ol></li> belongs to no block, and used to be skipped silently."""
    path = build_epub(str(tmp_path / "loose.epub"), chapters=[("One", LOOSE)])
    texts = [sentence.text for sentence in sentences_of(load_epub(path))]
    assert texts == [
        "The second item carries more weight.",
        "A nested item goes here.",
        "He walked on without looking back.",
        "Some Book, page four.",
    ]


def test_a_run_that_is_one_element_is_not_wrapped(tmp_path):
    """The markup only gains a span where there is bare text, so styling on <cite> survives."""
    path = build_epub(str(tmp_path / "cite.epub"), chapters=[("One", LOOSE)])
    package = load_epub(path)
    quote = next(
        element
        for element in package.docs[0].tree.getroot().iter()
        if local_name(element.tag) == "blockquote"
    )
    assert [local_name(child.tag) for child in quote] == ["p", "cite"]
