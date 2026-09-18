"""Command line entry point: ``readalign build`` and ``readalign check``."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time

from . import __version__
from .errors import InputError, ReadAlignError

DEFAULT_MODEL = "large-v3-turbo"
DEFAULT_DRIFT = 2.5


def _cache_root() -> str:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "readalign", "cache")
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "readalign")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="readalign",
        description="Turn a DRM-free audiobook and its ebook into one EPUB 3 with media overlays.",
    )
    parser.add_argument("--version", action="version", version=f"readalign {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="align an audiobook to an ebook and write an EPUB")
    build.add_argument("--audio", required=True,
                       help="audio file, or a directory of numbered files, or a glob")
    build.add_argument("--epub", required=True, help="the matching DRM-free EPUB")
    build.add_argument("--out", required=True, help="path of the EPUB to write")
    build.add_argument("--model", default=DEFAULT_MODEL,
                       help=f"faster-whisper model (default: {DEFAULT_MODEL})")
    build.add_argument("--language", default=None,
                       help="ISO language code of the narration (default: detect)")
    build.add_argument("--drift-threshold", type=float, default=DEFAULT_DRIFT, metavar="SECONDS",
                       help="fail if any sentence drifts more than this "
                            f"(default: {DEFAULT_DRIFT})")
    sidecars = build.add_mutually_exclusive_group()
    sidecars.add_argument("--sidecars", dest="sidecars", action="store_true", default=True,
                          help="write WebVTT and align.json next to the output (default)")
    sidecars.add_argument("--no-sidecars", dest="sidecars", action="store_false",
                          help="write only the EPUB and the report")
    build.add_argument("--dry-run", action="store_true",
                       help="probe the inputs and print the plan without transcribing")
    build.add_argument("--narrator", default=None, help="value for the media:narrator metadata")
    build.add_argument("--refine", action="store_true",
                       help="sharpen boundaries with the MMS CTC aligner "
                            "(needs readalign[refine]; that model is CC BY-NC 4.0)")
    build.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"],
                       help="where to run the recogniser (default: auto)")
    build.add_argument("--beam-size", type=int, default=5, help="whisper beam size (default: 5)")
    build.add_argument("--no-vad", dest="vad", action="store_false", default=True,
                       help="disable the voice-activity filter")
    build.add_argument("--audio-bitrate", default="96k",
                       help="bitrate used when audio has to be transcoded (default: 96k)")
    build.add_argument("--work-dir", default=None,
                       help="scratch directory to use instead of a temporary one")
    build.add_argument("--cache-dir", default=None,
                       help=f"transcript cache (default: {_cache_root()})")
    build.add_argument("--no-cache", dest="cache", action="store_false", default=True,
                       help="do not read or write the transcript cache")
    build.add_argument("--quiet", action="store_true", help="only print errors and the summary")

    check = subparsers.add_parser("check", help="validate the media overlays in an EPUB")
    check.add_argument("epub", help="the EPUB to inspect")
    check.add_argument("--json", action="store_true", help="print the findings as JSON")
    check.add_argument("--no-audio-probe", dest="probe", action="store_false", default=True,
                       help="skip the ffprobe duration check")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            return run_build(args)
        return run_check(args)
    except ReadAlignError as exc:
        print(f"readalign: {exc}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print("readalign: interrupted", file=sys.stderr)
        return 130
    except BrokenPipeError:  # pragma: no cover - only when output is piped into head
        return 0


def _dry_run_plan(package, tracks, log) -> int:
    log("")
    log("dry run, nothing will be transcribed or written")
    log(f"  ebook          {package.title() or '(untitled)'}  EPUB {package.version}")
    log(f"  documents      {len(package.docs)} of {len(package.spine_ids)} spine items carry text")
    log(f"  sentences      {sum(len(doc.sentences) for doc in package.docs)}")
    total = sum(track.duration for track in tracks)
    log(f"  audio          {len(tracks)} file(s), {total / 3600:.2f} hours")
    for track in tracks:
        note = " (transcode)" if track.transcoded else " (stream copy)"
        log(f"    {track.index:>3}  {os.path.basename(track.source)}  "
            f"{track.duration / 60:.1f} min  -> {track.media_type}{note}")
    return 0


def run_build(args) -> int:
    from .align import align
    from .audio import decode_wav, discover_inputs, prepare_tracks, require_ffmpeg
    from .epub import annotate_document, load_epub
    from .package import build_output, ensure_mimetype, write_epub
    from .report import build_report, document_stats, format_summary, write_report

    log = (lambda *_: None) if args.quiet else print
    started = time.monotonic()
    warnings: list[str] = []

    if args.drift_threshold <= 0:
        raise InputError("--drift-threshold must be a positive number of seconds")
    out_path = os.path.abspath(args.out)
    if os.path.isdir(out_path):
        raise InputError(f"--out points at a directory: {out_path}")

    require_ffmpeg()
    log(f"readalign {__version__}")
    log(f"reading {args.epub}")
    package = load_epub(args.epub)
    for index, doc in enumerate(package.docs):
        doc.sentences = annotate_document(doc.tree, index)
    sentences = [sentence for doc in package.docs for sentence in doc.sentences]
    if not sentences:
        raise InputError(
            f"{args.epub} has spine documents but no sentences in them. It may be a "
            "fixed-layout or image-only EPUB, which readalign cannot narrate."
        )
    log(f"  {len(package.docs)} content documents, {len(sentences)} sentences")

    sources = discover_inputs(args.audio)
    log(f"preparing {len(sources)} audio file(s)")
    work_dir = args.work_dir or tempfile.mkdtemp(prefix="readalign-")
    temporary = args.work_dir is None
    os.makedirs(work_dir, exist_ok=True)
    try:
        tracks = prepare_tracks(sources, work_dir, args.audio_bitrate, log=log)
        if args.dry_run:
            return _dry_run_plan(package, tracks, log)

        for track in tracks:
            decode_wav(track, work_dir)

        cache_dir = None
        if args.cache:
            cache_dir = args.cache_dir or _cache_root()
        log("transcribing")
        from .asr import transcribe_tracks

        words, asr_info = transcribe_tracks(
            tracks,
            model_name=args.model,
            device=args.device,
            language=args.language,
            vad=args.vad,
            beam_size=args.beam_size,
            cache_dir=cache_dir,
            log=log,
        )
        log(f"  {len(words)} words in {asr_info['seconds']}s on {asr_info['device']}")

        log("aligning")
        result = align(sentences, words, tracks, log=log)
        if args.refine:
            from .refine import refine

            asr_info["refine"] = refine(result, tracks, device=args.device, log=log)
            warnings.append(
                "built with --refine: the MMS-300m aligner is CC BY-NC 4.0, so these timings "
                "are for non-commercial use"
            )

        entries_by_doc: dict[str, list] = {}
        for entry in result.timed:
            doc = package.docs[entry.sentence.doc_index]
            entries_by_doc.setdefault(doc.manifest_id, []).append(entry)
        if not entries_by_doc:
            raise InputError(
                "no sentence could be timed, so there is nothing to write. Check that the audio "
                "and the ebook are the same work."
            )

        files, summary = build_output(package, entries_by_doc, tracks, narrator=args.narrator)
        ensure_mimetype(files)
        write_epub(out_path, files, package.order)
        log(f"wrote {out_path} ({os.path.getsize(out_path) / 1_048_576:.1f} MB, "
            f"{summary['overlays']} overlays, {summary['total_duration']})")

        stats = document_stats(package, result)
        for doc in package.docs:
            if doc.recovered:
                warnings.append(f"{doc.href} had malformed XML and was parsed in recovery mode")
            if doc.reencoded_from:
                warnings.append(f"{doc.href} was decoded as {doc.reencoded_from}, not UTF-8")
        if result.unaligned_regions:
            warnings.append(
                f"{len(result.unaligned_regions)} stretch(es) of the book had no match in the "
                "narration and were timed by interpolation"
            )
        report = build_report(
            package, result, tracks, stats,
            output=out_path,
            asr_info=asr_info,
            drift_threshold=args.drift_threshold,
            warnings=warnings,
            extras={"elapsed_seconds": round(time.monotonic() - started, 1),
                    "overlay_duration": summary["total_duration"]},
        )
        report_path = os.path.join(os.path.dirname(out_path) or ".", "readalign-report.json")
        write_report(report_path, report)

        if args.sidecars:
            from .sidecar import write_sidecars

            directory = os.path.join(
                os.path.dirname(out_path) or ".",
                os.path.splitext(os.path.basename(out_path))[0] + "-sidecars",
            )
            written = write_sidecars(
                directory, result, tracks,
                {index: doc.href for index, doc in enumerate(package.docs)},
                summary["audio_hrefs"],
                title=package.title(),
            )
            log(f"wrote {len(written)} sidecar file(s) to {directory}")
    finally:
        if temporary:
            shutil.rmtree(work_dir, ignore_errors=True)

    print(format_summary(report))
    print(f"  report         {report_path}")
    if not report["drift"]["within_threshold"]:
        print(
            f"readalign: max drift {report['drift']['max']:.2f}s exceeds the "
            f"--drift-threshold of {args.drift_threshold:.2f}s. The EPUB was still written; "
            f"check readalign-report.json for where it slipped.",
            file=sys.stderr,
        )
        return 3
    return 0


def _resolve(base: str, href: str) -> str:
    import posixpath
    from urllib.parse import unquote

    target = unquote(href.split("#", 1)[0])
    return posixpath.normpath(posixpath.join(posixpath.dirname(base), target))


def _open_package(archive, path: str):
    """Return the package document path and its parsed root, or raise a plain InputError."""
    from lxml import etree

    from .epub import CONTAINER_NS

    names = set(archive.namelist())
    if "META-INF/container.xml" not in names:
        raise InputError(f"{path} has no META-INF/container.xml, so it is not an EPUB")
    container = etree.fromstring(archive.read("META-INF/container.xml"))
    rootfile = container.find(f".//{{{CONTAINER_NS}}}rootfile")
    if rootfile is None or not rootfile.get("full-path"):
        raise InputError(f"{path} has no rootfile in META-INF/container.xml")
    opf_path = rootfile.get("full-path")
    if opf_path not in names:
        raise InputError(f"{path} names a package document that is missing: {opf_path}")
    return opf_path, etree.fromstring(archive.read(opf_path))


def _overlay_durations(
    opf, opf_ns: str, errors: list[str]
) -> tuple[dict[str, float], float | None]:
    from .clock import parse_clock

    durations: dict[str, float] = {}
    total: float | None = None
    for meta in opf.iter(f"{{{opf_ns}}}meta"):
        if meta.get("property") != "media:duration":
            continue
        try:
            value = parse_clock((meta.text or "").strip())
        except ValueError as exc:
            errors.append(f"media:duration is not a SMIL clock value: {exc}")
            continue
        refines = meta.get("refines")
        if refines:
            durations[refines.lstrip("#")] = value
        else:
            total = value
    return durations, total


def _fragment_ids(archive, doc_path: str, cache: dict[str, set[str]]) -> set[str]:
    from .epub import parse_content_document

    if doc_path not in cache:
        tree, _, _ = parse_content_document(archive.read(doc_path), doc_path)
        cache[doc_path] = {
            element.get("id")
            for element in tree.getroot().iter()
            if isinstance(element.tag, str) and element.get("id")
        }
    return cache[doc_path]


def _probe_clip_ends(archive, max_clip_end: dict[str, float], errors: list[str],
                     warnings: list[str]) -> dict[str, float]:
    """Acceptance check: no clipEnd may sit past the real duration ffprobe reports."""
    import posixpath

    from .audio import ffprobe

    durations: dict[str, float] = {}
    with tempfile.TemporaryDirectory(prefix="readalign-check-") as scratch:
        for audio_path, clip_end in sorted(max_clip_end.items()):
            extracted = os.path.join(scratch, posixpath.basename(audio_path))
            with open(extracted, "wb") as handle:
                handle.write(archive.read(audio_path))
            try:
                duration = float(ffprobe(extracted)["format"]["duration"])
            except (ReadAlignError, KeyError, ValueError, TypeError) as exc:
                warnings.append(f"could not probe {audio_path}: {exc}")
                continue
            durations[audio_path] = round(duration, 3)
            if clip_end > duration + 0.05:
                errors.append(
                    f"{audio_path}: a clipEnd of {clip_end:.3f}s is past the file's real "
                    f"duration of {duration:.3f}s"
                )
    return durations


def check_epub(path: str, *, probe_audio: bool = True) -> dict:
    """Validate the media overlays of an EPUB. Structural faults are reported, not raised."""
    import zipfile
    from urllib.parse import unquote

    from lxml import etree

    from .clock import parse_clock
    from .epub import OPF_NS
    from .smil import SMIL_NS

    errors: list[str] = []
    warnings: list[str] = []
    if not os.path.exists(path):
        raise InputError(f"no such file: {path}")
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise InputError(f"{path} is not a readable EPUB (not a zip archive): {exc}") from exc

    with archive:
        names = set(archive.namelist())
        entries = archive.infolist()
        if not entries or entries[0].filename != "mimetype":
            errors.append("the first archive entry is not 'mimetype'")
        elif entries[0].compress_type != zipfile.ZIP_STORED:
            errors.append("the 'mimetype' entry is compressed; OCF requires it stored")
        elif archive.read("mimetype") != b"application/epub+zip":
            errors.append("the 'mimetype' entry does not contain application/epub+zip")

        opf_path, opf = _open_package(archive, path)
        items = {item.get("id"): item for item in opf.iter(f"{{{OPF_NS}}}item") if item.get("id")}
        spine = [ref.get("idref") for ref in opf.iter(f"{{{OPF_NS}}}itemref") if ref.get("idref")]
        for idref in spine:
            if idref not in items:
                errors.append(f"spine itemref '{idref}' has no manifest item")
        durations, total_meta = _overlay_durations(opf, OPF_NS, errors)

        fragments: dict[str, set[str]] = {}
        max_clip_end: dict[str, float] = {}
        overlays = 0
        pars = 0
        clip_total = 0.0

        for idref in spine:
            item = items.get(idref)
            overlay_id = item.get("media-overlay") if item is not None else None
            if not overlay_id:
                continue
            overlays += 1
            overlay = items.get(overlay_id)
            if overlay is None:
                errors.append(
                    f"'{idref}' points at media-overlay '{overlay_id}', which is not in the "
                    "manifest"
                )
                continue
            if overlay.get("media-type") != "application/smil+xml":
                errors.append(
                    f"overlay '{overlay_id}' is declared as {overlay.get('media-type')}, not "
                    "application/smil+xml"
                )
            smil_path = _resolve(opf_path, overlay.get("href", ""))
            if smil_path not in names:
                errors.append(f"overlay '{overlay_id}' is missing from the archive: {smil_path}")
                continue
            if overlay_id not in durations:
                warnings.append(f"overlay '{overlay_id}' has no media:duration metadata")
            smil = etree.fromstring(archive.read(smil_path))
            overlay_clip = 0.0
            for par in smil.iter(f"{{{SMIL_NS}}}par"):
                pars += 1
                text = par.find(f"{{{SMIL_NS}}}text")
                audio = par.find(f"{{{SMIL_NS}}}audio")
                if text is None or not text.get("src"):
                    errors.append(f"{smil_path}: a par has no text src")
                    continue
                src = text.get("src")
                doc_path = _resolve(smil_path, src)
                fragment = unquote(src.split("#", 1)[1]) if "#" in src else ""
                if doc_path not in names:
                    errors.append(f"{smil_path}: text points at a missing document {doc_path}")
                elif fragment and fragment not in _fragment_ids(archive, doc_path, fragments):
                    errors.append(f"{smil_path}: no element '{fragment}' in {doc_path}")
                if audio is None or not audio.get("src"):
                    errors.append(f"{smil_path}: a par has no audio src")
                    continue
                audio_path = _resolve(smil_path, audio.get("src"))
                if audio_path not in names:
                    errors.append(f"{smil_path}: audio points at a missing file {audio_path}")
                    continue
                try:
                    begin = parse_clock(audio.get("clipBegin", "0s"))
                    end = parse_clock(audio.get("clipEnd", "0s"))
                except ValueError as exc:
                    errors.append(f"{smil_path}: {exc}")
                    continue
                if end <= begin:
                    errors.append(f"{smil_path}: clipEnd {end} is not after clipBegin {begin}")
                overlay_clip += max(0.0, end - begin)
                max_clip_end[audio_path] = max(max_clip_end.get(audio_path, 0.0), end)
            clip_total += overlay_clip
            declared = durations.get(overlay_id)
            if declared is not None and abs(declared - overlay_clip) > 1.0:
                warnings.append(
                    f"overlay '{overlay_id}' declares {declared:.3f}s but its clips total "
                    f"{overlay_clip:.3f}s"
                )

        audio_durations = (
            _probe_clip_ends(archive, max_clip_end, errors, warnings)
            if probe_audio and max_clip_end
            else {}
        )
        if overlays == 0:
            errors.append("no spine document has a media-overlay attribute")
        if total_meta is None and overlays:
            warnings.append("the package has no unrefined media:duration for the whole book")

    return {
        "epub": os.path.abspath(path),
        "overlays": overlays,
        "sentences": pars,
        "clip_seconds": round(clip_total, 3),
        "declared_total": round(total_meta, 3) if total_meta is not None else None,
        "audio_durations": audio_durations,
        "errors": errors,
        "warnings": warnings,
        "ok": not errors,
    }


def run_check(args) -> int:
    import json

    findings = check_epub(args.epub, probe_audio=args.probe)
    if args.json:
        print(json.dumps(findings, ensure_ascii=False, indent=2))
        return 0 if findings["ok"] else 1

    print(f"checking {findings['epub']}")
    print(f"  overlays       {findings['overlays']}")
    print(f"  sentences      {findings['sentences']}")
    print(f"  clip time      {findings['clip_seconds'] / 3600:.2f} hours")
    if findings["declared_total"] is not None:
        print(f"  media:duration {findings['declared_total'] / 3600:.2f} hours declared")
    for warning in findings["warnings"][:20]:
        print(f"  warning        {warning}")
    for error in findings["errors"][:20]:
        print(f"  error          {error}", file=sys.stderr)
    hidden = len(findings["errors"]) - 20
    if hidden > 0:
        print(f"  error          and {hidden} more", file=sys.stderr)
    print("  result         " + ("ok" if findings["ok"] else "failed"))
    return 0 if findings["ok"] else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
