# readalign

Turn an audiobook you own and the matching ebook you own into a single EPUB 3 with conformant
Media Overlays: the words highlight as the narrator reads them, in any reading system that
supports EPUB 3 read-aloud.

Everything runs on your machine. There is no account, no API key and no upload. The only network
access is the one-time download of the speech model.

```
readalign build --audio audiobook/ --epub book.epub --out read-along.epub
```

## What it produces

- **an EPUB 3.3 file** containing your ebook, the audio, one SMIL overlay per chapter, the
  `media-overlay` links, `media:duration` metadata and a highlight stylesheet. It passes
  epubcheck 5.2.1 with zero messages.
- **`readalign-report.json`** next to the output: coverage, drift, per-document statistics, which
  documents got no overlay and which regions never matched.
- **sidecars** (`<name>-sidecars/`): one WebVTT file per chapter and audio file, plus `align.json`
  with every sentence's id, clip times, confidence and text. A rerun clears the `.vtt` files and
  `align.json` it wrote last time, so nothing stale is left behind. Turn them off with
  `--no-sidecars`.

## Requirements

- Python 3.11 or newer
- **ffmpeg and ffprobe on `PATH`** (readalign shells out to them; it does not vendor a copy).
  Get them from <https://ffmpeg.org/download.html>.
- An NVIDIA GPU is optional. With one, a 7 hour book transcribes in about 12 minutes. On CPU the
  same book takes hours, so use a smaller `--model` there. If a GPU is present but its CUDA
  libraries will not load, readalign says so and carries on on the CPU.

## Install

```
pip install readalign
```

or, without installing anything permanently:

```
uvx readalign build --audio ... --epub ... --out ...
```

## Quick start

A public-domain pair is bundled in `examples/aesop`: two minutes of a LibriVox recording and the
matching Project Gutenberg text. From a checkout:

```
readalign build \
    --audio examples/aesop/aesop-section-37.mp3 \
    --epub examples/aesop/aesop-two-fables.epub \
    --out aligned.epub --model tiny.en --language en --device cpu
```

Two minutes of audio is not worth spinning up a GPU for, hence `--device cpu`. Real output
from that command, on a cold cache:

```
readalign 1.0.0
reading examples/aesop/aesop-two-fables.epub
  2 content documents, 18 sentences
preparing 1 audio file(s)
  [1/1] aesop-section-37.mp3 -> stream copy
transcribing
  loading whisper model 'tiny.en' on cpu (int8)
  [1/1] aesop-section-37.mp3: transcribing 1:58
      298 words so far, 1:58/1:58 audio in 0:03 (35.8x realtime)
  298 words in 3.3s on cpu
aligning
  aligning 245 book tokens against 300 spoken tokens
wrote D:\Repos\ideas\readalign\aligned.epub (0.9 MB, 2 overlays, 0:01:11.640)
wrote 3 sidecar file(s) to D:\Repos\ideas\readalign\aligned-sidecars
  sentences      18/18 timed (100.0%)
  interpolated   0   low confidence 0
  token match    95.1%
  drift          max 0.00s, p95 0.00s, threshold 2.50s
  report         D:\Repos\ideas\readalign\readalign-report.json
```

Run it again and the transcribing lines become `298 words from cache`.

The recording opens and closes with the LibriVox announcement, which has no counterpart in the
text. readalign trims it: the first overlay starts at 20.5 seconds.

To fetch the sample yourself, or a full-length pair to try:

```
python scripts/fetch_samples.py short    # rebuilds examples/aesop
python scripts/fetch_samples.py long     # Frankenstein, 7.5 hours over 25 files
```

## Checking the result

```
$ readalign check aligned.epub
checking D:\Repos\ideas\readalign\aligned.epub
  overlays       2
  sentences      18
  clip time      0.02 hours
  media:duration 0.02 hours declared
  result         ok
```

`check` walks the package document, opens every overlay, and verifies that each `text` element
points at a fragment that exists in a manifested content document, that each `audio` element
points at a manifested audio file, that `clipEnd` never runs past the real duration ffprobe
reports for that file, and that the declared `media:duration` values match the clips. It exits
non-zero if anything fails, and takes `--json` if you want to script it.

It is worth running the official validator too:

```
java -jar epubcheck.jar aligned.epub
```

## A full-length run

The 25-file LibriVox recording of *Frankenstein* (7 h 26 m) against the Standard Ebooks edition,
on an RTX 4090:

```
reading frankenstein-se.epub
  38 content documents, 3403 sentences
preparing 25 audio file(s)
transcribing
  loading whisper model 'large-v3-turbo' on cuda (float16)
  [1/25] frankenstein_00_shelley_64kb.mp3: transcribing 32:56
  ...
  75799 words in 718.6s on cuda
aligning
  aligning 78488 book tokens against 75827 spoken tokens
wrote frankenstein.epub (205.0 MB, 30 overlays, 6:59:25.256)
wrote 44 sidecar file(s) to frankenstein-sidecars
  sentences      3250/3403 timed (95.5%)
  interpolated   9   low confidence 10
  token match    94.0%
  drift          max 3.98s, p95 0.00s, threshold 2.50s
  untimed matter 128 sentences before the narration, 25 after
  no overlay     8 document(s): text/titlepage.xhtml, text/imprint.xhtml, ...
```

Twelve minutes of transcription, about ninety seconds for everything else. The result passes
epubcheck with no messages and `readalign check` reports the same 3250 sentences the build wrote.

That run exits 3, not 0, and the reason is worth understanding. The 8 documents with no overlay
are the title page, the imprint, Shelley's 1831 introduction, the preface, the dedication, the
epigraph, the colophon and the copyright page. The narrator reads none of them, so readalign
times none of them. The 153 untimed sentences are the same material. The max drift of 3.98s comes
from two sentences in the endnotes, which the narrator also never reads: one endnote matched a
phrase in the closing credits, so the endnote heading next to it got placed inside a 4 second
window instead of being left alone. Everything the narrator actually reads has a drift of zero.
`--drift-threshold 4` makes that run exit 0; the report tells you exactly which sentences moved
the number, so you can decide rather than guess.

## Drift, and what the number means

Drift is how far a sentence's start could be wrong, in seconds.

- A sentence anchored on its own narrated words has drift **0**. Its start is a word timestamp
  from the recogniser, accurate to a few tens of milliseconds.
- A sentence the narrator skipped, or garbled past recognition, is placed proportionally in the
  gap between its neighbours. Its drift is the width of that gap: the worst it can be out by.
- A sentence whose start had to be pushed forward to keep the overlay in order carries the
  distance it was pushed. That is the signal for text matched to the wrong occurrence of itself.

`--drift-threshold` (default 2.5s) is a gate on the maximum, not the average. The EPUB is always
written; exceeding the threshold only changes the exit code to 3 so a script can catch it.

## How it works

1. **ffprobe** reads every audio file. FLAC, WAV, ALAC and WMA are transcoded to AAC because
   EPUB 3 only takes MP3, MP4 audio and Opus; MP3, M4A/M4B and Opus are stream-copied, so no
   audio is re-encoded unless it has to be.
2. **faster-whisper** transcribes with word timestamps. Transcripts are cached per file, keyed by
   content and settings, so a second run over the same audiobook costs nothing.
3. Both sides are normalised to comparable tokens: case folded, accents stripped, punctuation
   dropped, digits and roman numerals spelled out ("Chapter XVIII" and "chapter eighteen" become
   the same tokens).
4. **Alignment** pins the two streams together with n-grams that occur exactly once on each side,
   keeps the longest increasing chain of them, and runs a real diff only in the small regions
   between anchors. That is what lets a 20 hour book align in a few hundred megabytes rather than
   needing a 200k x 200k dynamic programming matrix.
5. The XHTML is rewritten so each sentence is wrapped in `<span id="ra-1-7">`. Existing ids are
   never touched, and sentence boundaries that fall inside `<em>` or `<a>` are split without
   changing a character of the text.
6. Overlays, manifest items and metadata go into the package document, and the archive is written
   mimetype-first and uncompressed as OCF requires.

## Options

```
--audio PATH             a file, a directory of numbered files, or a glob
--epub PATH              the matching DRM-free EPUB
--out PATH               the EPUB to write
--model NAME             faster-whisper model (default: large-v3-turbo)
--language CODE          ISO code of the narration (default: detect)
--drift-threshold SECS   exit 3 if any sentence drifts more than this (default: 2.5)
--sidecars/--no-sidecars write WebVTT and align.json next to the output (default: on)
--dry-run                probe the inputs and print the plan, transcribe nothing
--narrator NAME          value for the media:narrator metadata
--refine                 sharpen boundaries with the MMS CTC aligner (see below)
--device auto|cuda|cpu   where to run the recogniser (default: auto)
--beam-size N            whisper beam size (default: 5)
--no-vad                 disable the voice-activity filter
--audio-bitrate RATE     bitrate when audio has to be transcoded (default: 96k)
--work-dir PATH          scratch directory instead of a temporary one
--cache-dir PATH         transcript cache location
--no-cache               neither read nor write the transcript cache
--quiet                  only print errors and the summary
```

Multiple files are ordered by natural sort, so `part2.mp3` comes before `part10.mp3`. A single
file covering the whole book is fine, and so is a chapter split over several files; readalign
works from a continuous timeline and does not assume one file per chapter.

### The optional refine pass

`pip install 'readalign[refine]'` adds torch and torchaudio, and `--refine` then runs a CTC
forced-alignment pass over each sentence boundary to sharpen it.

**The MMS-300m aligner that pass uses is licensed CC BY-NC 4.0, which means non-commercial use
only.** It is never downloaded unless you pass `--refine`, nothing in the default path touches
it, and any run that uses it records the restriction in the report's warnings.

## Limitations

- **No DRM, ever.** An `.aax`, `.aa` or `.acsm` file, or an EPUB with an `META-INF/encryption.xml`,
  exits 2 with `readalign does not handle DRM-protected files`. readalign will not strip
  protection and has no plans to.
- Output is only as good as the recognition. Heavy accents, music beds, dramatised multi-voice
  productions and poetry read with long pauses all lower the token match rate; watch that number
  in the report.
- Abridged recordings work, in the sense that the parts that are read get timed and the rest is
  reported as untimed. They will never reach full coverage.
- Audio is embedded in the EPUB, so the file is as large as the audiobook. Frankenstein at 64 kbps
  is 205 MB. There is no streaming or external-audio option in this version.
- The audio timeline is shared across files. A sentence never spans two audio files; if one would,
  it is clipped at the file boundary.
- Sentence splitting is tuned for English-style punctuation. Other languages align fine but may
  produce longer or shorter units than a native reader would choose.

## Reader support

Media Overlays are part of EPUB 3, but support is uneven. Readium-based readers (Thorium Reader
on desktop, Colibrio-based web readers) and Apple Books implement them. Kindle does not read EPUB
overlays at all, and several popular mobile readers ignore the `media-overlay` attribute and just
show the book. If a reader shows the text but plays no audio, it does not support overlays; the
file is still a valid EPUB and still readable.

readalign's output is validated against epubcheck 5.2.1, and `readalign check` is validated
against the reference Media Overlays title from the EPUB test suite at
<https://epubtest.org/test-books>.

## Exit codes

| code | meaning |
| ---- | ------- |
| 0 | success |
| 1 | bad input, missing file, missing ffmpeg, nothing could be aligned |
| 2 | the input is DRM-protected |
| 3 | the EPUB was written but max drift exceeded `--drift-threshold` |
| 130 | interrupted |

Failures print one line, not a traceback.

## Development

```
git clone https://github.com/cbosch101/readalign
cd readalign
pip install -e '.[dev]'
python -m pytest tests -q
python -m ruff check .
```

The suite runs in about a second and needs neither a GPU nor network access; it builds its own
EPUBs and synthesises word timings. A few cases need ffmpeg and skip without it, and one is
skipped unless `READALIGN_MO_TESTBOOK` points at a copy of the epubtest.org Media Overlays book.

## Distribution

The first distribution step is PyPI: `python -m build` then `twine upload dist/*`, which makes
`pip install readalign` and `uvx readalign` work for everyone. Nothing else in the project depends
on a hosted service.

## Licence

MIT. See [LICENSE](LICENSE).

The bundled sample in `examples/aesop` is public domain: the recording from LibriVox, the text
from Project Gutenberg. See `examples/aesop/README.md` for the sources.
