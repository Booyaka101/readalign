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
| Test suite | `python -m pytest tests -q` -> 69 passed, 1 skipped; with `READALIGN_MO_TESTBOOK` set, 70 passed |
| Lint | `python -m ruff check .` -> All checks passed |
| Clean install | wheel installed into an empty venv, then `--version`, a full `build` and a `check` run from a directory outside the repo. Repeated after the last source change |
| CUDA fallback | that venv has no `cublas64_12.dll`; the build warns once, names the wheels to install, and finishes on the CPU with exit 0 |
| Sidecar rerun | rebuilding Frankenstein over an older sidecar directory leaves exactly the 44 files it reports, with no leftovers from the previous track split |
| Clone check | 167 functions compared pairwise with difflib, nested bodies excluded from their parent's span. Highest pair 0.47, both sides pytest fixtures in different modules. Nothing in `src/` above 0.45 |
| Memory | Frankenstein peak working set 157.2 MB. Audio is streamed into the archive rather than held: before the change the same build peaked at 316.8 MB and produced a byte-identical file |

## Deferred, in rough order of usefulness

1. **Escapability.** Skippability ships: footnotes, endnotes, page numbers, sidebars and
   annotations already come out inside a `seq` carrying their `epub:type`, so "skip footnotes"
   works. Escapability does not. Those `seq` elements are flat siblings under `body` rather than
   mirroring the XHTML's nesting, so a reader cannot offer "escape this table" from three levels
   in. It needs the SMIL builder to walk the document tree instead of the sentence list, and no
   sample book here exercises it, which is why it was not attempted blind.
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

1. Publish to PyPI: `python -m build` then `twine upload dist/*`. `https://pypi.org/pypi/readalign/json`
   returned 404 on 2026-09-18, so the name is free; check it again just before the upload. The
   owner does this from the phone.
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
- Six commits so far, on `master`, no remote.
