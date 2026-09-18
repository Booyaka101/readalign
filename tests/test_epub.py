"""Loading, sentence annotation and the refusals that must not turn into stack traces."""

import pytest
from lxml import etree

from conftest import DOC, build_epub
from readalign.epub import annotate_document, load_epub, parse_content_document
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


def annotate_all(package):
    for index, doc in enumerate(package.docs):
        doc.sentences = annotate_document(doc.tree, index)
    return [sentence for doc in package.docs for sentence in doc.sentences]


def test_loads_and_annotates(sample_epub):
    package = load_epub(sample_epub)
    sentences = annotate_all(package)
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
    annotate_all(package)
    for doc in package.docs:
        assert flatten(doc.tree.getroot()) == before[doc.zip_path]


def test_ids_are_stable_across_runs(sample_epub):
    first = [sentence.fragment_id for sentence in annotate_all(load_epub(sample_epub))]
    second = [sentence.fragment_id for sentence in annotate_all(load_epub(sample_epub))]
    assert first == second


def test_existing_ids_are_never_overwritten(tmp_path):
    path = build_epub(
        str(tmp_path / "ids.epub"),
        chapters=[("One", '<p id="keep-me">A single sentence stands alone.</p>')],
    )
    sentences = annotate_all(load_epub(path))
    assert [sentence.fragment_id for sentence in sentences] == ["keep-me"]


def test_footnote_gets_its_epub_type(sample_epub):
    sentences = annotate_all(load_epub(sample_epub))
    footnotes = [sentence for sentence in sentences if sentence.epub_type]
    assert len(footnotes) == 1
    assert footnotes[0].epub_type == "footnote"
    assert footnotes[0].container_id == "fn1"


def test_noteref_marker_does_not_merge_sentences(sample_epub):
    texts = [sentence.text for sentence in annotate_all(load_epub(sample_epub))]
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
    sentences = annotate_all(package)
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
    assert annotate_all(load_epub(path))[0].text == "The café was closed."


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


def test_epub_2_is_refused_with_a_hint(tmp_path):
    path = build_epub(str(tmp_path / "old.epub"), version="2.0")
    with pytest.raises(InputError) as info:
        load_epub(path)
    assert "EPUB 3" in str(info.value)
