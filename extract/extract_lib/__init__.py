"""extract_lib — Layer C of aws-llm.

Ported from the working ``~/call-logs/extract_lib`` reference (local 3090).
The canonical row schema lives in :mod:`schema` (nested ``Extraction``
model + AWS row wrapper); see ``PLAN.md`` (§Data layer, §Spot handling,
§Run config) for design rationale.
"""

from __future__ import annotations

__version__ = "0.1.0"

from .schema import (  # noqa: F401
    SCHEMA_VERSION,
    SYSTEM_PROMPT,
    AgentAssessment,
    BENCH_MEASUREMENT_FIELDS,
    CallEvents,
    CallExtraction,
    CallOutcome,
    CustomerAssessment,
    EXPERIMENT_RUNS_FIELDS,
    Extraction,
    QualityFlags,
    extraction_json_schema,
    get_arrow_schema,
)
from .features import (  # noqa: F401
    call_id_from_path,
    category_from_path,
    compute_features,
    compute_features_from_text,
    load_transcript,
)

__all__ = [
    # Submodules — kept for backwards compatibility with code that does
    # ``from extract_lib import schema``.
    "schema",
    "client",
    "iceberg_writer",
    "runner",
    "config",
    "spot_watcher",
    "cloudwatch_metrics",
    "features",
    # Top-level re-exports
    "SCHEMA_VERSION",
    "SYSTEM_PROMPT",
    "AgentAssessment",
    "BENCH_MEASUREMENT_FIELDS",
    "CallEvents",
    "CallExtraction",
    "CallOutcome",
    "CustomerAssessment",
    "EXPERIMENT_RUNS_FIELDS",
    "Extraction",
    "QualityFlags",
    "extraction_json_schema",
    "get_arrow_schema",
    "call_id_from_path",
    "category_from_path",
    "compute_features",
    "compute_features_from_text",
    "load_transcript",
]
