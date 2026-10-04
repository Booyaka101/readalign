# readalign progress

**State: 1.1.0 is released.** Everything in the brief is built, the suite is green, and both
sample books build, validate and check out. A review pass after the release closed the first
deferred item (escapability), fixed text that was silently never narrated, and cut the memory
the loader and the checker use. A second pass fixed re-runs stacking stale audio and manifest
ids, a transcript cache that ignored `--beam-size`, sentence splitting after the word "no",
made `--audio` globs actually work and broken XML in `check` a finding instead of a traceback,
and added `--verify` plus report excerpts (see CHANGELOG). v1.1.0 is tagged and pushed with
both artefacts attached to the GitHub release, and 1.1.0 is on PyPI. 1.0.0 was uploaded to
PyPI on 2026-09-18 from the v1.0.0 release artefacts.

Last updated 2026-10-04.

## Verified working

Each line below was run on this machine and the output read, not assumed.

| What | Evidence |
| ---- | -------- |
| Short end-to-end build | `examples/aesop`, tiny.en on CPU: exit 0, 18/18 sentences timed, token match 95.1%, max drift 0.00s |
| Long end-to-end build | 25 LibriVox Frankenstein files (7:26:11) against the Standard Ebooks text, large-v3-turbo on an RTX 4090: 3250/3404 timed, 75799 words in 517.6s (51.7x realtime), max drift 3.98s, 205.0 MB, 30 overlays, exit 3 (drift gate, see README) |
| epubcheck 5.2.1 | both outputs: `0 fatals / 0 errors / 0 warnings / 0 infos` |
| OCF packaging | `zipfile` one-liner from the brief passes on both outputs: `mimetype` first and `ZIP_STORED` |
| `readalign check` | reports 18 and 3250 sentences, the same counts the two builds wrote; probes every `clipEnd` against ffprobe |
| Reference title | `readalign check` on the epubtest.org Media Overlays book reports ok (gated behind `READALIGN_MO_TESTBOOK`) |
| Test suite | `python -m pytest tests -q` -> 106 passed, 1 skipped; with `READALIGN_MO_TESTBOOK` set, 107 passed |
| Lint | `python -m ruff check .` -> All checks passed |
| 1.1.0 release | built from `13d0274`: `twine check` passed on both artefacts; the wheel installed into an empty venv, `readalign --version` and `python -m readalign --version` say 1.1.0, and the Aesop build from that install reproduces the `d998e692...3f98` hash; after upload, `pip install readalign` from PyPI in a third venv also says 1.1.0 |
| Clean install | wheel installed into an empty venv, then `--version`, a full `build` and a `check` run from a directory outside the repo. Repeated after the last source change |
| CUDA fallback | that venv has no `cublas64_12.dll`; the build warns once, names the wheels to install, and finishes on the CPU with exit 0 |
| Sidecar rerun | rebuilding Frankenstein over an older sidecar directory leaves exactly the 44 files it reports, with no leftovers from the previous track split |
| Clone check | 190 functions compared pairwise with difflib, nested bodies excluded from their parent's span. No pair anywhere at or above 0.45 |
| Memory | Frankenstein build peak working set 129.9 MB, down from 316.8 MB before audio was streamed into the archive and 157.2 MB before untouched members were carried by reference. Opening the 205 MB result costs 33.5 MB, down from 238.7 MB; `check` on it costs 38.7 MB, down from 58.4 MB. Every step was proved byte-identical on the way |
| Reproducible output | three separate builds of Frankenstein from the same inputs all hash to `f1f185f2...d639c`; the Aesop example hashes to `d998e692...3f98` across every refactor in this pass. A hash is only comparable within one transcript, so reproduce these with the flags the row above names (`--model tiny.en --device cpu` for Aesop). A different model or compute type transcribes differently and hashes differently, which is not a regression |
| Bad input | twelve wrong-input paths run against the installed wheel: missing file, a directory, a non-zip, an EPUB 2 package, an `.acsm`, a book with an AES `encryption.xml`, a missing or empty audio directory and a file ffprobe rejects. Each gives one line and exits 1, or 2 for DRM. No traceback reaches the user |
| Escapability | tables, rows, cells, lists, list items and figures come out as nested `seq` elements mirroring the XHTML, and epubcheck 5.2.1 accepts the `epub:type` values it sees on them |

## Deferred, in rough order of usefulness

1. **Audio outside the EPUB.** Every output embeds the audio, so a 7 hour book is a 200 MB file.
   A mode that writes the overlays against audio hrefs the user hosts, or a second EPUB with the
   audio stripped, would suit anyone syncing to a phone. Changes the packaging model, so it is a
   feature release, not a patch.
2. **A chapter map.** `--chapters map.json` to pin document to audio file by hand, for abridged
   recordings and for books where the narration order does not follow the spine. Today the
   aligner works it out from the text and usually gets it right; when it does not, the user has no
   lever.
3. **Per-language sentence splitting.** Splitting is tuned for English-style punctuation. Books in
   Japanese, Thai or Arabic align but produce units a native reader would not have chosen.
4. **`--verify`**, running epubcheck automatically when a jar is on `PATH` or `JAVA_HOME` is set.
   Small, but it adds a Java prerequisite to the happy path, so it stays opt-in if it is built.
5. **Proofing output.** A side-by-side text diff of book against narration for the regions that
   did not match, to make a bad match obvious without opening the JSON.
6. **Multi-voice productions.** Dramatised recordings with several actors and music beds lower the
   token match rate. Nothing here is specific to them.

## Next steps

1. Confirm `uvx readalign --version` works somewhere uv is installed (pip-from-PyPI was
   confirmed here).
2. Announce wherever audiobook and accessibility people are, with the Frankenstein numbers.

## Working notes

- `D:/tmp/readalign-data` holds the sample downloads, the epubcheck jar and the build logs. It is
  outside the repo on purpose: `scripts/fetch_samples.py long` rebuilds it in a few minutes.
- The transcript cache lives in `%LOCALAPPDATA%/readalign/cache`. The 25 Frankenstein tracks are
  in it, so a rebuild of that book takes about 90 seconds instead of twelve minutes.
- Thirty-one commits on `main` at <https://github.com/Booyaka101/readalign>. v1.0.0 is tagged at
  `3d4884a` and v1.1.0 at `13d0274`, both released there with artefacts attached. CI runs ruff and
  the suite on Linux
  3.11, Linux 3.12 and Windows; Windows is in the matrix because `zipfile` reports a directory as
  `PermissionError` there and `IsADirectoryError` everywhere else.
- CI shows 106 passed 1 skipped. The skip is the epubtest.org Media Overlays book, which needs
  `READALIGN_MO_TESTBOOK`; locally with it set the count is 107 passed.
