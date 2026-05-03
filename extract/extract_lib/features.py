"""Deterministic per-call features computed without the LLM.

Ported verbatim from ``~/call-logs/scripts/extract_lib/features.py``.

These run even when vLLM is down — they're cheap and reusable. Anything
requiring semantic understanding lives in the LLM extraction; anything
derivable from text + word timestamps lives here.

The 14 fields produced match the ``features`` struct in
``CALL_EXTRACTIONS_ARROW_SCHEMA`` (see ``schema.py``).
"""

from __future__ import annotations

import json
import re
import statistics
from pathlib import Path
from typing import Any

__all__ = [
    "compute_features",
    "compute_features_from_text",
    "call_id_from_path",
    "category_from_path",
    "load_transcript",
]

# AIxBlock PII placeholder format: [PERSON_NAME], [LOCATION], etc.
_REDACTION_RE = re.compile(r"\[[A-Z_]+\]")
# Crude turn detection: speaker changes are not labeled, so we use sentence-ish
# boundaries as a coarse proxy. Don't over-interpret this number.
_TURN_BOUNDARY_RE = re.compile(r"[.?!]\s+")


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    qs = statistics.quantiles(values, n=100, method="inclusive")
    idx = max(1, min(99, int(pct))) - 1
    return qs[idx]


def compute_features(transcript: dict[str, Any]) -> dict[str, Any]:
    """Compute deterministic features from a transcript JSON dict.

    Word timestamps are in milliseconds; ``audio_duration`` is in seconds
    in the AIxBlock dataset.
    """
    text: str = transcript.get("text", "") or ""
    overall_conf: float = float(transcript.get("confidence", 0.0) or 0.0)
    duration_sec: float = float(transcript.get("audio_duration", 0.0) or 0.0)
    words: list[dict[str, Any]] = transcript.get("words", []) or []

    char_count = len(text)
    word_count = len(words) if words else len(text.split())
    turn_count = len(_TURN_BOUNDARY_RE.split(text)) if text else 0
    redacted_count = len(_REDACTION_RE.findall(text))

    confidences = [float(w.get("confidence", 0.0) or 0.0) for w in words]
    if confidences:
        conf_mean = statistics.fmean(confidences)
        conf_min = min(confidences)
        conf_p10 = _percentile(sorted(confidences), 10)
        low_conf_pct = sum(1 for c in confidences if c < 0.7) / len(confidences)
    else:
        conf_mean = conf_min = conf_p10 = 0.0
        low_conf_pct = 0.0

    # Silence gaps: time between consecutive words where end_i < start_{i+1}.
    silence_total_ms = 0
    silence_max_ms = 0
    if len(words) >= 2:
        for prev, curr in zip(words, words[1:]):
            gap = int(curr.get("start", 0) or 0) - int(prev.get("end", 0) or 0)
            if gap > 0:
                silence_total_ms += gap
                if gap > silence_max_ms:
                    silence_max_ms = gap

    silence_total_sec = silence_total_ms / 1000.0
    silence_max_sec = silence_max_ms / 1000.0
    silence_pct = (silence_total_sec / duration_sec) if duration_sec > 0 else 0.0
    wpm = (word_count / duration_sec * 60.0) if duration_sec > 0 else 0.0

    return {
        "char_count": char_count,
        "word_count": word_count,
        "turn_count_estimate": turn_count,
        "audio_duration_sec": duration_sec,
        "words_per_minute": wpm,
        "asr_confidence_overall": overall_conf,
        "word_confidence_mean": conf_mean,
        "word_confidence_min": conf_min,
        "word_confidence_p10": conf_p10,
        "low_confidence_word_pct": low_conf_pct,
        "silence_total_sec": silence_total_sec,
        "silence_max_gap_sec": silence_max_sec,
        "silence_pct_of_call": silence_pct,
        "redacted_token_count": redacted_count,
    }


def compute_features_from_text(text: str) -> dict[str, Any]:
    """Degraded variant for inputs without word timestamps.

    Fills text-derived fields and zeros out the ASR / silence / WPM
    fields. Use only when the upstream pipeline genuinely lacks
    word-level timing (e.g., a JSONL of bare transcript strings).
    """
    text = text or ""
    return compute_features({"text": text})


def call_id_from_path(path: Path) -> str:
    """Stable id derived from filename stem (drops ``_transcript`` suffix)."""
    stem = path.stem
    if stem.endswith("_transcript"):
        stem = stem[: -len("_transcript")]
    return stem


def category_from_path(path: Path, dataset_root: Path) -> str:
    """Category is the first directory below ``dataset_root``."""
    try:
        rel = path.relative_to(dataset_root)
    except ValueError:
        return path.parent.name
    return rel.parts[0] if rel.parts else path.parent.name


def load_transcript(path: Path) -> dict[str, Any]:
    with path.open() as f:
        return json.load(f)
