"""Build readalign-report.json and the short summary the CLI prints.

Everything a reader would want to check before trusting the output goes here: how much of the
book got timed, where the alignment drifted, and which chapters got no overlay at all.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass

from .align import CONFIDENT, AlignmentResult
from .audio import AudioTrack
from .epub import EpubPackage
from .fs import ensure_directory, write_text

REPORT_VERSION = 1


@dataclass
class DocStat:
    """Per content document: how much of it ended up narrated."""

    manifest_id: str
    href: str
    sentences: int
    aligned: int
    max_drift: float
    start: float | None
    end: float | None


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[position]


def document_stats(package: EpubPackage, result: AlignmentResult) -> list[DocStat]:
    timed_by_doc: dict[int, list] = {}
    for entry in result.timed:
        timed_by_doc.setdefault(entry.sentence.doc_index, []).append(entry)
    stats = []
    for index, doc in enumerate(package.docs):
        entries = timed_by_doc.get(index, [])
        stats.append(
            DocStat(
                manifest_id=doc.manifest_id,
                href=doc.href,
                sentences=len(doc.sentences),
                aligned=len(entries),
                max_drift=round(max((entry.drift for entry in entries), default=0.0), 3),
                start=round(min(entry.start for entry in entries), 3) if entries else None,
                end=round(max(entry.end for entry in entries), 3) if entries else None,
            )
        )
    return stats


def build_report(
    package: EpubPackage,
    result: AlignmentResult,
    tracks: list[AudioTrack],
    stats: list[DocStat],
    *,
    output: str,
    asr_info: dict,
    drift_threshold: float,
    warnings: list[str],
    extras: dict | None = None,
) -> dict:
    drifts = [entry.drift for entry in result.timed]
    total_sentences = sum(len(doc.sentences) for doc in package.docs)
    max_drift = round(max(drifts, default=0.0), 3)
    return {
        "version": REPORT_VERSION,
        "tool": "readalign",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "input": {
            "epub": os.path.abspath(package.source),
            "title": package.title(),
            "epub_version": package.version,
            "audio_files": [os.path.basename(track.source) for track in tracks],
            "audio_duration": round(sum(track.duration for track in tracks), 3),
            "transcoded": [
                os.path.basename(track.source) for track in tracks if track.transcoded
            ],
        },
        "output": os.path.abspath(output),
        "asr": asr_info,
        "coverage": {
            "sentences": total_sentences,
            "aligned": len(result.timed),
            "unaligned": total_sentences - len(result.timed),
            "interpolated": sum(1 for entry in result.timed if entry.interpolated),
            "low_confidence": sum(1 for entry in result.timed if entry.confidence < CONFIDENT),
            "clamped_to_track_end": sum(1 for entry in result.timed if entry.clamped),
            "trimmed_head": result.trimmed_head,
            "trimmed_tail": result.trimmed_tail,
            "token_match_rate": round(
                result.matched_tokens / result.book_tokens if result.book_tokens else 0.0, 4
            ),
        },
        "drift": {
            "threshold": drift_threshold,
            "max": max_drift,
            "p95": round(_percentile(drifts, 0.95), 3),
            "mean": round(sum(drifts) / len(drifts), 3) if drifts else 0.0,
            "within_threshold": max_drift <= drift_threshold,
        },
        "documents": [stat.__dict__ for stat in stats],
        "documents_without_overlay": [stat.href for stat in stats if stat.aligned == 0],
        "unaligned_regions": result.unaligned_regions,
        "warnings": warnings,
        **(extras or {}),
    }


def write_report(path: str, report: dict) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    ensure_directory(directory)
    write_text(path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")


def format_summary(report: dict) -> str:
    coverage = report["coverage"]
    drift = report["drift"]
    missing = report["documents_without_overlay"]
    lines = [
        f"  sentences      {coverage['aligned']}/{coverage['sentences']} timed "
        f"({coverage['aligned'] / max(coverage['sentences'], 1):.1%})",
        f"  interpolated   {coverage['interpolated']}"
        f"   low confidence {coverage['low_confidence']}",
        f"  token match    {coverage['token_match_rate']:.1%}",
        f"  drift          max {drift['max']:.2f}s, p95 {drift['p95']:.2f}s, "
        f"threshold {drift['threshold']:.2f}s",
    ]
    if coverage["trimmed_head"] or coverage["trimmed_tail"]:
        lines.append(
            f"  untimed matter {coverage['trimmed_head']} sentences before the narration, "
            f"{coverage['trimmed_tail']} after"
        )
    if missing:
        rest = f" and {len(missing) - 5} more" if len(missing) > 5 else ""
        shown = ", ".join(missing[:5]) + rest
        lines.append(f"  no overlay     {len(missing)} document(s): {shown}")
    for warning in report["warnings"]:
        lines.append(f"  warning        {warning}")
    return "\n".join(lines)
