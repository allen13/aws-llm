"""Extraction schema (Pydantic) and Iceberg row schema (PyArrow).

Ported from ``~/call-logs/scripts/extract_lib/schema.py`` (the working local
3090 reference) with AWS-specific extensions (``run_id`` partition column,
``extracted_at`` timestamp, ``model_id`` for served-name tracking).

The Pydantic ``Extraction`` model is the structured-output target the LLM
fills in. We do **not** use vLLM ``guided_json`` constrained decoding —
that path is empirically broken on this checkpoint (whitespace loops, see
``client.py`` for why). Instead the system prompt enumerates every field
in plain language and we validate the response with Pydantic post-hoc.

The PyArrow ``CALL_EXTRACTIONS_ARROW_SCHEMA`` mirrors the Pydantic shape
1:1 so PyIceberg can land the row as nested struct columns queryable from
Athena v3 with dot-notation.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional

try:
    from pydantic import BaseModel, Field, ValidationError  # type: ignore

    _PYDANTIC_AVAILABLE = True
except Exception:  # pragma: no cover — keep import smoke-test green
    _PYDANTIC_AVAILABLE = False

    class ValidationError(Exception):  # type: ignore[no-redef]
        pass

    class BaseModel:  # type: ignore[no-redef]
        """Minimal stand-in so the smoke test passes without pydantic.

        Real runs install pydantic via the ``aws-llm-extract`` deps; this
        fallback only matters for ``import extract_lib`` correctness on a
        dev machine without the optional deps installed.
        """

        model_fields: dict[str, object] = {}

        def __init__(self, **kwargs):
            cls = type(self)
            field_names: list[str] = []
            for klass in reversed(cls.__mro__):
                ann = getattr(klass, "__annotations__", {})
                for name in ann:
                    if name in {"model_fields"}:
                        continue
                    if name not in field_names:
                        field_names.append(name)
            for name in field_names:
                if name in kwargs:
                    setattr(self, name, kwargs[name])
                elif hasattr(cls, name):
                    val = getattr(cls, name)
                    setattr(self, name, val() if callable(val) and name == "extracted_at" else val)
                else:
                    setattr(self, name, None)
            type(self).model_fields = {n: None for n in field_names}

        def model_dump(self) -> dict:
            return {k: getattr(self, k, None) for k in type(self).model_fields}

        @classmethod
        def model_validate(cls, data: Any) -> "BaseModel":
            if isinstance(data, cls):
                return data
            return cls(**(data or {}))

        @classmethod
        def model_json_schema(cls) -> dict:
            return {"title": cls.__name__, "type": "object"}

        def __repr__(self) -> str:
            kvs = ", ".join(
                f"{k}={getattr(self, k, None)!r}" for k in type(self).model_fields
            )
            return f"{type(self).__name__}({kvs})"

    def Field(default=None, default_factory=None, **_kw):  # type: ignore[no-redef]
        if default_factory is not None:
            return default_factory
        return default


# ----------------------------------------------------------------------------
# Schema version
# ----------------------------------------------------------------------------

# Bump on any change to the Pydantic ``Extraction`` shape (new field, type
# change, removed field). Old rows remain queryable via the ``schema_version``
# column. The PyArrow ``CALL_EXTRACTIONS_ARROW_SCHEMA`` below must change
# in lock-step.
SCHEMA_VERSION = "2.0.0"


# ----------------------------------------------------------------------------
# Pydantic models — leaf classes ported verbatim from the reference
# ----------------------------------------------------------------------------

Sentiment = Literal["positive", "neutral", "negative"]
Effort = Literal["low", "medium", "high"]
ResolutionStatus = Literal[
    "resolved",
    "unresolved",
    "escalated",
    "callback_scheduled",
    "transferred",
    "no_action_needed",
]


class AgentAssessment(BaseModel):
    name: Optional[str] = Field(None, description="Agent name if mentioned (PII placeholders OK)")
    professionalism_score: int = Field(..., ge=1, le=5)
    empathy_score: int = Field(..., ge=1, le=5)
    knowledge_score: int = Field(..., ge=1, le=5, description="Apparent product/process knowledge")
    greeting_compliant: Optional[bool] = None
    closing_compliant: Optional[bool] = None
    identified_themselves: bool = False
    identified_company: bool = False
    used_customer_name: bool = False
    apologized_when_appropriate: Optional[bool] = None
    interrupted_customer: bool = False


class CustomerAssessment(BaseModel):
    intent_primary: str = Field(..., description="Short string label for primary reason for call")
    sentiment_start: Sentiment = "neutral"
    sentiment_end: Sentiment = "neutral"
    satisfaction_score: int = Field(..., ge=1, le=5, description="Inferred CSAT proxy 1-5")
    effort_score: Effort = Field(..., description="CES-style effort the customer had to expend")
    explicitly_thanked: bool = False
    explicitly_complained: bool = False
    requested_supervisor: bool = False


class CallOutcome(BaseModel):
    resolution_status: ResolutionStatus = "no_action_needed"
    resolution_summary: str = Field(..., description="1-2 sentence summary of how the call concluded")
    first_call_resolution: bool = False
    complexity_score: int = Field(..., ge=1, le=5)
    authentication_completed: Optional[bool] = None


class CallEvents(BaseModel):
    hold_time_mentioned: bool = False
    transfer_occurred: bool = False
    callback_scheduled: bool = False
    upsell_attempted: bool = False
    payment_collected: bool = False
    pii_disclosed_unnecessarily: bool = False


class QualityFlags(BaseModel):
    language_barrier_detected: bool = False
    profanity_or_abuse: bool = False
    information_accuracy_concerns: bool = False
    audio_quality_issues_mentioned: bool = False


class Extraction(BaseModel):
    """Full LLM-extracted view of a single call.

    Mirrors the reference exactly — ``client.py`` validates raw JSON
    responses against this model and short-circuits to an error row on
    ``ValidationError``.
    """

    agent: AgentAssessment
    customer: CustomerAssessment
    outcome: CallOutcome
    events: CallEvents
    quality: QualityFlags
    summary: str = Field(..., description="2-3 sentence neutral summary of the call")
    key_quotes: list[str] = Field(default_factory=list, max_length=3, description="Up to 3 notable verbatim quotes")
    coaching_opportunities: list[str] = Field(
        default_factory=list,
        max_length=5,
        description="Specific actionable feedback for the agent",
    )


def extraction_json_schema() -> dict:
    """JSON schema for the ``Extraction`` model — used by mock servers
    and would-be ``guided_json`` callers."""
    return Extraction.model_json_schema()


# ----------------------------------------------------------------------------
# Iceberg row model — wraps ``Extraction`` plus AWS-specific telemetry
# ----------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CallExtraction(BaseModel):  # type: ignore[misc,valid-type]
    """One row in the Iceberg ``calls_extractions`` table.

    Wraps the nested ``Extraction`` from the LLM with: routing keys
    (``call_id``, ``run_id``), provenance (``source_path``,
    ``category_path``, ``model_id``, ``schema_version``), deterministic
    pre-LLM features, and per-call telemetry (token counts, truncation,
    error message). Status is one of ``success | error | skipped``.
    """

    call_id: str
    run_id: str  # partition column

    source_path: Optional[str] = None
    category_path: Optional[str] = None
    transcript_text: Optional[str] = None

    features: Optional[dict] = None
    extraction: Optional[Extraction] = None

    extraction_status: Literal["success", "error", "skipped"] = "success"
    error_message: Optional[str] = None

    model_id: Optional[str] = None
    schema_version: str = SCHEMA_VERSION

    transcript_length_chars: Optional[int] = None
    was_truncated: bool = False
    prompt_token_count: Optional[int] = None
    completion_token_count: Optional[int] = None

    extracted_at: datetime = Field(default_factory=_utcnow)


# ----------------------------------------------------------------------------
# System prompt — verbatim hand-written field-by-field spec
# ----------------------------------------------------------------------------
#
# Why not ``response_format: json_schema``: vLLM 0.20 + xgrammar gets stuck
# in a whitespace loop on this nested ~50-field schema — verified
# empirically on the reference.
#
# Why not ``response_format: json_object``: the model emits whitespace
# forever after the closing ``}`` until ``max_tokens`` is hit, producing
# truncated/unparseable output — verified empirically.
#
# The model stops cleanly when given a clear plain-language spec.
# Identical bytes across every request → benefits from prefix caching
# (PLAN.md §Validation gates #3 needs hit-rate > 0.7).

SYSTEM_PROMPT: str = """You are a call-center QA analyst. Output JSON ONLY (no prose, markdown, or code fences). PII placeholders ([PERSON_NAME], [LOCATION], [DATE_OF_BIRTH], …) are redactions — treat them as unknown values, not literals. Ground every judgment in transcript evidence; use neutral values or null when a field is genuinely unknowable.

Schema (every key required, exact shape):

{
  "agent": {
    "name": str|null,
    "professionalism_score": 1-5, "empathy_score": 1-5, "knowledge_score": 1-5,
    "greeting_compliant": bool|null, "closing_compliant": bool|null,
    "identified_themselves": bool, "identified_company": bool, "used_customer_name": bool,
    "apologized_when_appropriate": bool|null,
    "interrupted_customer": bool
  },
  "customer": {
    "intent_primary": str,
    "sentiment_start": "positive"|"neutral"|"negative",
    "sentiment_end":   "positive"|"neutral"|"negative",
    "satisfaction_score": 1-5,
    "effort_score": "low"|"medium"|"high",
    "explicitly_thanked": bool, "explicitly_complained": bool, "requested_supervisor": bool
  },
  "outcome": {
    "resolution_status": "resolved"|"unresolved"|"escalated"|"callback_scheduled"|"transferred"|"no_action_needed",
    "resolution_summary": str,
    "first_call_resolution": bool,
    "complexity_score": 1-5,
    "authentication_completed": bool|null
  },
  "events": {
    "hold_time_mentioned": bool, "transfer_occurred": bool, "callback_scheduled": bool,
    "upsell_attempted": bool, "payment_collected": bool, "pii_disclosed_unnecessarily": bool
  },
  "quality": {
    "language_barrier_detected": bool, "profanity_or_abuse": bool,
    "information_accuracy_concerns": bool, "audio_quality_issues_mentioned": bool
  },
  "summary": "2-3 sentences",
  "key_quotes": ["up to 3 verbatim"],
  "coaching_opportunities": ["up to 5 actionable items"]
}"""


# ----------------------------------------------------------------------------
# PyArrow / Iceberg schema
# ----------------------------------------------------------------------------
#
# Mirror of the Pydantic structure so PyIceberg lands the row as nested
# struct columns. Athena v3 reads them via dot-notation
# (``extraction.agent.empathy_score``). Build lazily so ``import schema``
# doesn't require pyarrow on minimal dev machines.

CALL_EXTRACTIONS_ARROW_SCHEMA: Any = None


def _build_arrow_schema():
    import pyarrow as pa  # type: ignore

    agent_struct = pa.struct([
        pa.field("name", pa.string()),
        pa.field("professionalism_score", pa.int8()),
        pa.field("empathy_score", pa.int8()),
        pa.field("knowledge_score", pa.int8()),
        pa.field("greeting_compliant", pa.bool_()),
        pa.field("closing_compliant", pa.bool_()),
        pa.field("identified_themselves", pa.bool_()),
        pa.field("identified_company", pa.bool_()),
        pa.field("used_customer_name", pa.bool_()),
        pa.field("apologized_when_appropriate", pa.bool_()),
        pa.field("interrupted_customer", pa.bool_()),
    ])
    customer_struct = pa.struct([
        pa.field("intent_primary", pa.string()),
        pa.field("sentiment_start", pa.string()),
        pa.field("sentiment_end", pa.string()),
        pa.field("satisfaction_score", pa.int8()),
        pa.field("effort_score", pa.string()),
        pa.field("explicitly_thanked", pa.bool_()),
        pa.field("explicitly_complained", pa.bool_()),
        pa.field("requested_supervisor", pa.bool_()),
    ])
    outcome_struct = pa.struct([
        pa.field("resolution_status", pa.string()),
        pa.field("resolution_summary", pa.string()),
        pa.field("first_call_resolution", pa.bool_()),
        pa.field("complexity_score", pa.int8()),
        pa.field("authentication_completed", pa.bool_()),
    ])
    events_struct = pa.struct([
        pa.field("hold_time_mentioned", pa.bool_()),
        pa.field("transfer_occurred", pa.bool_()),
        pa.field("callback_scheduled", pa.bool_()),
        pa.field("upsell_attempted", pa.bool_()),
        pa.field("payment_collected", pa.bool_()),
        pa.field("pii_disclosed_unnecessarily", pa.bool_()),
    ])
    quality_struct = pa.struct([
        pa.field("language_barrier_detected", pa.bool_()),
        pa.field("profanity_or_abuse", pa.bool_()),
        pa.field("information_accuracy_concerns", pa.bool_()),
        pa.field("audio_quality_issues_mentioned", pa.bool_()),
    ])
    extraction_struct = pa.struct([
        pa.field("agent", agent_struct),
        pa.field("customer", customer_struct),
        pa.field("outcome", outcome_struct),
        pa.field("events", events_struct),
        pa.field("quality", quality_struct),
        pa.field("summary", pa.string()),
        pa.field("key_quotes", pa.list_(pa.string())),
        pa.field("coaching_opportunities", pa.list_(pa.string())),
    ])
    features_struct = pa.struct([
        pa.field("char_count", pa.int32()),
        pa.field("word_count", pa.int32()),
        pa.field("turn_count_estimate", pa.int32()),
        pa.field("audio_duration_sec", pa.float32()),
        pa.field("words_per_minute", pa.float32()),
        pa.field("asr_confidence_overall", pa.float32()),
        pa.field("word_confidence_mean", pa.float32()),
        pa.field("word_confidence_min", pa.float32()),
        pa.field("word_confidence_p10", pa.float32()),
        pa.field("low_confidence_word_pct", pa.float32()),
        pa.field("silence_total_sec", pa.float32()),
        pa.field("silence_max_gap_sec", pa.float32()),
        pa.field("silence_pct_of_call", pa.float32()),
        pa.field("redacted_token_count", pa.int32()),
    ])

    return pa.schema([
        pa.field("call_id", pa.string(), nullable=False),
        pa.field("run_id", pa.string(), nullable=False),
        pa.field("source_path", pa.string()),
        pa.field("category_path", pa.string()),
        pa.field("transcript_text", pa.string()),
        pa.field("features", features_struct),
        pa.field("extraction", extraction_struct),
        pa.field("extraction_status", pa.string()),
        pa.field("error_message", pa.string()),
        pa.field("model_id", pa.string()),
        pa.field("schema_version", pa.string()),
        pa.field("transcript_length_chars", pa.int32()),
        pa.field("was_truncated", pa.bool_()),
        pa.field("prompt_token_count", pa.int32()),
        pa.field("completion_token_count", pa.int32()),
        pa.field("extracted_at", pa.timestamp("us", tz="UTC")),
    ])


def get_arrow_schema():
    """Lazily build and cache the Arrow schema. Defers ``import pyarrow``."""
    global CALL_EXTRACTIONS_ARROW_SCHEMA
    if CALL_EXTRACTIONS_ARROW_SCHEMA is None:
        CALL_EXTRACTIONS_ARROW_SCHEMA = _build_arrow_schema()
    return CALL_EXTRACTIONS_ARROW_SCHEMA


# ----------------------------------------------------------------------------
# Sibling-table column types — consumed by iceberg_writer to build Iceberg
# Schema objects. Types are spelled in the Iceberg vocabulary so the writer
# can map them 1:1.
# ----------------------------------------------------------------------------

BENCH_MEASUREMENT_FIELDS: dict[str, str] = {
    "run_id": "string",
    "concurrency": "int",
    "req_per_sec": "double",
    "error_rate": "double",
    "prefix_cache_hit_ratio": "double",
    "gpu_util_mean": "double",
    "total_calls": "int",
    "duration_seconds": "double",
    "measured_at": "timestamp",
}

EXPERIMENT_RUNS_FIELDS: dict[str, str] = {
    "run_id": "string",
    "test_name": "string",
    "git_sha": "string",
    "model_manifest_hash": "string",
    "vllm_image_uri": "string",
    "instance_type": "string",
    "use_spot": "boolean",
    "region": "string",
    "enable_thinking": "boolean",
    "max_model_len": "int",
    "max_num_seqs": "int",
    "started_at": "timestamp",
    "ended_at": "timestamp",  # nullable while running
    "status": "string",  # running | success | partial | failed | interrupted
    "notes": "string",
}
