from conftest import build_epub, make_tracks
from readalign.align import TimedSentence
from readalign.epub import load_epub
from readalign.smil import continuous_clips


def test_continuous_clips_preserve_adjacent_clips_and_outer_boundaries(tmp_path):
    package = load_epub(build_epub(str(tmp_path / "input.epub")))
    sentences = package.docs[0].sentences
    tracks = make_tracks([20])
    entries = [TimedSentence(sentences[0], 2, 4, 1.0, 1),
               TimedSentence(sentences[1], 4, 7, 1.0, 1)]
    assert continuous_clips(entries, tracks) == entries
    assert continuous_clips([], tracks) == []
    assert continuous_clips(entries[:1], tracks) == entries[:1]


def test_gap_ending_at_track_boundary_does_not_add_zero_length_clip(tmp_path):
    package = load_epub(build_epub(str(tmp_path / "input.epub")))
    sentences = package.docs[0].sentences
    tracks = make_tracks([10, 10])
    entries = [TimedSentence(sentences[0], 2, 4, 1.0, 1),
               TimedSentence(sentences[1], 10, 12, 1.0, 2)]
    clips = continuous_clips(entries, tracks)
    assert [(entry.start, entry.end, entry.track_index) for entry in clips] == [
        (2, 10, 1), (10, 12, 2)]
