# Changelog

## 1.0.0 - 2026-09-18

First release.

- `readalign build` turns a DRM-free audiobook and the matching DRM-free EPUB into one EPUB 3
  with conformant Media Overlays: SMIL per content document, `media-overlay` on the manifest
  items, `media:duration` per overlay plus a total, active-class metadata and a highlight
  stylesheet. Footnotes, endnotes, page numbers, sidebars and annotations go inside a `seq`
  carrying their `epub:type`, so a reading system can offer to skip them. The archive is
  written mimetype-first and uncompressed as OCF requires.
- `readalign check` validates the overlays in any EPUB: fragment ids resolve, audio is
  manifested, no `clipEnd` runs past the duration ffprobe reports, declared durations match the
  clips. `--json` for scripting.
- Transcription with faster-whisper, CUDA when available and CPU otherwise, word timestamps,
  and a per-file transcript cache keyed by content and settings. A GPU that reports itself
  present but cannot load its CUDA libraries falls back to the CPU with one warning line
  naming what to install, rather than a traceback.
- Anchored alignment: unique shared n-grams, longest increasing subsequence, diff only in the
  gaps. No global dynamic-programming matrix, so a 20 hour book stays in a few hundred megabytes.
- Sidecars: one WebVTT per chapter and audio file, plus `align.json`. `readalign-report.json`
  records coverage, drift, per-document statistics and every region that did not match.
- Handles one file spanning several chapters, several files in one chapter, audio chapter counts
  that disagree with the spine, narrator front and back matter with no text counterpart, notes
  and page-break markers, existing ids, non-UTF8 XHTML, and clip ends past the real duration.
- DRM-protected input (`.aax`, `.aa`, `.acsm`, an encrypted EPUB) exits 2 and is never opened.
- Optional `readalign[refine]` extra adds a CTC forced-alignment pass behind `--refine`. The
  MMS-300m aligner it uses is CC BY-NC 4.0, so it is never downloaded by the default path.
