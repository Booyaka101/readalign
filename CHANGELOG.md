# Changelog

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
