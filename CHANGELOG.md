# Changelog

## Unreleased

- New: `--audio-format mp3` stream-copies existing MP3 sources and transcodes other codecs
  to MP3 for readers with AAC/Opus decoder limitations. The default `auto` retains the existing
  behavior. Timing uses the prepared audio and the report records the selected format.
- Fixed: streamed audiobook entries now declare their size before ZIP writing so large files
  select ZIP64 headers instead of failing at the end of the write. Small entries retain their
  existing archive format.
- Added long-book packaging regressions and evidence-linked reader guidance distinguishing
  codec support, large-track loading, sentence highlighting and narration-position restoration.

## 1.2.0 - 2026-10-22

- New: `--report PATH` selects a separate JSON report for each batch build, with guards
  against overwriting input audio, the input/output EPUB or generated sidecars.
- Fixed: colliding overlay names and URL-special characters in EPUB resource paths.
  Generated audio and CSS also avoid overwriting unrelated existing resources.
- Fixed: transcript cache identity now hashes prepared audio bytes and includes chapter
  boundaries and the effective language. Malformed cached words are ignored instead of crashing.
- Fixed: EPUB, report and cache writes replace their destinations only after a complete write.
- Fixed: work-directory audio aliases, invalid durations, chapter marks and beam sizes are
  rejected; missing epubcheck paths fail before transcription.
- Fixed: `check` rejects unmanifested audio, overlays pointing outside their associated document,
  empty overlays, duplicate manifest ids and mismatched durations. Corrupt archives produce
  readable errors; audio-probe scratch filenames never come from archive member names.
- New: `--min-token-match FRACTION` optionally gates builds on the fraction of book tokens
  matched to narration. Values range from 0 to 1; the default 0 preserves existing behaviour.
  A failed gate exits 5 after writing the EPUB, report and enabled sidecars. Its threshold and
  verdict are recorded under `token_match_gate`; drift and epubcheck failures retain precedence.
- Fixed: chapters with identical basenames in different EPUB directories could overwrite each
  other's WebVTT sidecars. Colliding names now gain a numeric suffix, including case-only
  collisions on Windows, without changing filenames for non-colliding exports.
- Fixed: the ebook overwrite guard now recognises symbolic links, hard links and path aliases.
- Fixed: non-finite drift thresholds such as NaN and infinity are rejected before processing.

## 1.1.0 - 2026-10-04

- New: `--verify [JAR]` runs epubcheck on the result right after the build, prints its verdict,
  records it under `verify` in the report, and exits 4 when it finds errors. epubcheck needs
  Java, so the flag is opt-in: with a path it uses that jar, without one it searches `PATH`,
  `EPUBCHECK_HOME` and `JAVA_HOME`.
- New: every unaligned region in the report carries an `excerpt` quoting the book's own words,
  so a stretch that never matched the narration is recognisable without a token diff.
- Fixed: `--audio` accepted a glob in the documentation but not in the code, so a pattern like
  `book-*.mp3` failed with "audio not found" on Windows (and anywhere the shell was not asked
  to expand it). Patterns are now expanded by readalign itself, in natural order, with the same
  DRM refusal as a directory.
- Fixed: `readalign check` raised a raw traceback on input that was a valid zip but broken XML:
  an unparseable `META-INF/container.xml` or package document is now one line and exit 1, and
  an unparseable overlay or content document is reported as a finding like any other fault
  instead of crashing the walk.
- Fixed: rebuilding over an EPUB that already carried overlays kept the previous run's audio
  manifest item, so a re-run whose prepared audio changed name or format shipped the old file
  as dead weight (a full extra audiobook when the suffix drifted). A re-run now drops the
  previous audio and reuses the same manifest ids, the way it already did for overlays and the
  stylesheet.
- Fixed: manifest items added while writing an output were not registered in the package's
  manifest index, so two builds over one package could hand out the same item id twice.
- Fixed: the transcript cache key ignored `--beam-size` (and the window plan), so changing the
  beam silently served a transcript made with the old setting. Keys now cover every setting
  that changes the transcription, which invalidates caches written by 1.0.0 once.
- Fixed: a sentence ending in the word "no" never split from the next one ("He said no. She
  left."), because "no" sat in the abbreviation table. Numbered labels like "No. 5" still stay
  together: the digit after the period is not an uppercase word.
- Fixed: the warning for a failed cache write bypassed the `--quiet` log and printed straight
  to stderr; it now goes through the log like every other progress line.
- DRM refusals name the file they refused, which matters when a directory of audio contains
  one protected straggler.
- `readalign check` says "and N more" after its first twenty warnings, as it already did for
  errors, instead of truncating silently.

## 1.0.0 - 2026-09-18

First release.

- `readalign build` turns a DRM-free audiobook and the matching DRM-free EPUB into one EPUB 3
  with conformant Media Overlays: SMIL per content document, `media-overlay` on the manifest
  items, `media:duration` per overlay plus a total, active-class metadata and a highlight
  stylesheet. The overlay's `seq` elements mirror the document's nesting, so both skippability
  (footnotes, endnotes, page numbers, sidebars, annotations) and escapability (tables, lists,
  figures) work. The archive is written mimetype-first and uncompressed as OCF requires.
- `readalign check` validates the overlays in any EPUB: fragment ids resolve, audio is
  manifested, no `clipEnd` runs past the duration ffprobe reports, declared durations match the
  clips. `--json` for scripting.
- Transcription with faster-whisper, CUDA when available and CPU otherwise, word timestamps,
  and a per-file transcript cache keyed by content and settings. A GPU that reports itself
  present but cannot load its CUDA libraries falls back to the CPU with one warning line
  naming what to install, rather than a traceback.
- Anchored alignment: unique shared n-grams, longest increasing subsequence, diff only in the
  gaps. No global dynamic-programming matrix, and audio is streamed rather than buffered on the
  way both in and out, so a 7.5 hour book builds in about 130 MB of memory and opening the
  205 MB result costs 33 MB. Two runs of the same build produce byte-identical archives.
- Sidecars: one WebVTT per chapter and audio file, plus `align.json`. `readalign-report.json`
  records coverage, drift, per-document statistics and every region that did not match.
- Handles one file spanning several chapters, several files in one chapter, audio chapter counts
  that disagree with the spine, narrator front and back matter with no text counterpart, notes
  and page-break markers, existing ids, non-UTF8 XHTML, and clip ends past the real duration.
- DRM-protected input (`.aax`, `.aa`, `.acsm`, an encrypted EPUB) exits 2 and is never opened.
- Wrong input gets one line and an exit code, never a traceback: a missing file, an unzipped
  book passed as a directory, something that is not a zip, an EPUB 2 package, an empty audio
  directory, or a file ffprobe cannot read.
- Optional `readalign[refine]` extra adds a CTC forced-alignment pass behind `--refine`. The
  MMS-300m aligner it uses is CC BY-NC 4.0, so it is never downloaded by the default path.
