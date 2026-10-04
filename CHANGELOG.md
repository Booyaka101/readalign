# Changelog

## Unreleased

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
