# readalign progress

**State: v1.0.0 is complete and unpublished.** Everything in the brief is built, the suite is
green, and both sample books build, validate and check out. Nothing has been pushed to a remote
or uploaded anywhere. The next action belongs to the owner: publish to PyPI.

Last updated 2026-09-18.

## Verified working

Each line below was run on this machine and the output read, not assumed.

| What | Evidence |
| ---- | -------- |
| Short end-to-end build | `examples/aesop`, tiny.en on CPU: exit 0, 18/18 sentences timed, token match 95.1%, max drift 0.00s |
| Long end-to-end build | 25 LibriVox Frankenstein files (7:26:11) against the Standard Ebooks text, large-v3-turbo on an RTX 4090: 3250/3403 timed, 75799 words in 718.6s (~37x realtime), max drift 3.98s, 205.0 MB, 30 overlays, exit 3 (drift gate, see README) |
| epubcheck 5.2.1 | both outputs: `0 fatals / 0 errors / 0 warnings / 0 infos` |
| OCF packaging | `zipfile` one-liner from the brief passes on both outputs: `mimetype` first and `ZIP_STORED` |
| `readalign check` | reports 18 and 3250 sentences, the same counts the two builds wrote; probes every `clipEnd` against ffprobe |
| Reference title | `readalign check` on the epubtest.org Media Overlays book reports ok (gated behind `READALIGN_MO_TESTBOOK`) |
| Test suite | `python -m pytest tests -q` -> 69 passed, 1 skipped |
| Lint | `python -m ruff check .` -> All checks passed |
| Clean install | wheel installed into an empty venv at `D:/tmp/readalign-cleaninstall`, then `--version`, `check` and a full `build` run from a directory outside the repo |
| CUDA fallback | that venv has no `cublas64_12.dll`; the build warns once, names the wheels to install, and finishes on the CPU with exit 0 |
| Sidecar rerun | rebuilding Frankenstein over an older sidecar directory leaves exactly the 44 files it reports, with no leftovers from the previous track split |
| Clone check | 138 functions compared pairwise with difflib; one pair at 0.57 (two report tests), nothing in `src/` above 0.50 |

## Deferred, in rough order of usefulness

1. **Skippability and escapability.** EPUB 3 Media Overlays lets a reading system skip page
   numbers, footnotes and sidebars by wrapping them in a `seq` carrying the matching `epub:type`.
   readalign writes one flat `seq` of `par` elements, so a reader cannot offer "skip footnotes".
   This is the largest remaining gap against the spec and the one an accessibility reviewer would
   raise first. It needs the SMIL builder to follow the XHTML's structural nesting rather than the
   sentence list.
2. **Audio outside the EPUB.** Every output embeds the audio, so a 7 hour book is a 200 MB file.
   A mode that writes the overlays against audio hrefs the user hosts, or a second EPUB with the
   audio stripped, would suit anyone syncing to a phone. Changes the packaging model, so it is a
   feature release, not a patch.
3. **A chapter map.** `--chapters map.json` to pin document to audio file by hand, for abridged
   recordings and for books where the narration order does not follow the spine. Today the
   aligner works it out from the text and usually gets it right; when it does not, the user has no
   lever.
4. **Per-language sentence splitting.** Splitting is tuned for English-style punctuation. Books in
   Japanese, Thai or Arabic align but produce units a native reader would not have chosen.
5. **`--verify`**, running epubcheck automatically when a jar is on `PATH` or `JAVA_HOME` is set.
   Small, but it adds a Java prerequisite to the happy path, so it stays opt-in if it is built.
6. **Proofing output.** A side-by-side text diff of book against narration for the regions that
   did not match, to make a bad match obvious without opening the JSON.
7. **Multi-voice productions.** Dramatised recordings with several actors and music beds lower the
   token match rate. Nothing here is specific to them.

## Next steps

1. Publish to PyPI: `python -m build` then `twine upload dist/*`. The name `readalign` was free at
   the time of writing; check it again before the upload. The owner does this from the phone.
2. Create the GitHub repository `cbosch101/readalign` and push. The URLs in `pyproject.toml`
   already point there, so the PyPI page will link correctly once it exists.
3. After publishing, confirm `pip install readalign` and `uvx readalign --version` work from a
   machine that has never seen the source.
4. Announce wherever audiobook and accessibility people are, with the Frankenstein numbers.

## Working notes

- `D:/tmp/readalign-data` holds the sample downloads, the epubcheck jar and the build logs. It is
  outside the repo on purpose: `scripts/fetch_samples.py long` rebuilds it in a few minutes.
- The transcript cache lives in `%LOCALAPPDATA%/readalign/cache`. The 25 Frankenstein tracks are
  in it, so a rebuild of that book takes about 90 seconds instead of twelve minutes.
- Two commits so far, on `master`, no remote.
