"""Run-time configuration — env-var-driven."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_str(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _env_int(key: str, default: int) -> int:
    return int(os.environ.get(key, str(default)))


def _env_bool(key: str, default: bool) -> bool:
    return os.environ.get(key, "true" if default else "false").lower() == "true"


@dataclass
class ExtractConfig:
    """Configuration for the extraction pipeline.

    All values come from environment variables with sensible defaults;
    ``extract_batch.py`` may override individual fields from CLI flags.
    """

    vllm_endpoint: str = field(
        default_factory=lambda: _env_str("VLLM_ENDPOINT", "http://localhost:8000")
    )
    model_name: str = field(
        default_factory=lambda: _env_str("MODEL_NAME", "nemotron-nano-30b")
    )
    aws_region: str = field(
        default_factory=lambda: _env_str("AWS_REGION", "us-east-1")
    )
    tables_bucket_arn: str = field(
        default_factory=lambda: _env_str("TABLES_BUCKET_ARN", "")
    )
    tables_namespace: str = field(
        default_factory=lambda: _env_str("TABLES_NAMESPACE", "nemo")
    )

    # Default 5000 matches the reference's tuned value for max_model_len=8192
    # with v2 schema budget (system prompt ~480 tok + transcript ~1320 tok +
    # max_tokens 1200 = ~3000 tok, leaves headroom for prefix-cache slop).
    max_transcript_chars: int = field(
        default_factory=lambda: _env_int("MAX_TRANSCRIPT_CHARS", 5_000)
    )
    concurrency: int = field(
        default_factory=lambda: _env_int("CONCURRENCY", 32)
    )
    request_timeout_s: int = field(
        default_factory=lambda: _env_int("REQUEST_TIMEOUT_S", 120)
    )

    enable_thinking: bool = field(
        default_factory=lambda: _env_bool("ENABLE_THINKING", False)
    )

    pidfile_path: str = "/tmp/aws-llm-extract.pid"

    commit_every_n_rows: int = 1000
    commit_every_n_seconds: int = 30
