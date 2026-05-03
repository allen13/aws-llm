"""Parameterized SQL templates for the aws-llm Athena Lambda.

Templates are the safe path for operators: each parameter is type-checked or
regex-validated before interpolation. The free-form ``sql`` field on the Lambda
exists for ad-hoc exploration; templates exist so paper figures can't be
poisoned by a fat-fingered run_id.
"""

from __future__ import annotations

import re


_RUN_ID_RE = re.compile(r"[a-zA-Z0-9_-]{1,128}")
_SIMPLE_RE = re.compile(r"[a-zA-Z0-9_]{1,64}")


def _validate_run_id(s: str) -> str:
    if not isinstance(s, str) or not _RUN_ID_RE.fullmatch(s):
        raise ValueError(f"invalid run_id: {s!r}")
    return s


def _validate_simple_string(s: str) -> str:
    if not isinstance(s, str) or not _SIMPLE_RE.fullmatch(s):
        raise ValueError(f"invalid identifier: {s!r}")
    return s


def render(name: str, params: dict) -> str:
    """Render a named template into a SQL string.

    Raises ValueError on unknown template name or invalid parameters.
    """
    params = params or {}

    if name == "list_runs":
        limit = int(params.get("limit", 20))
        return (
            "SELECT * FROM nemo.experiment_runs "
            f"ORDER BY started_at DESC LIMIT {limit}"
        )

    if name == "summarize_run":
        run_id = _validate_run_id(params["run_id"])
        return f"""
            SELECT extraction_status,
                   COUNT(*) AS n,
                   AVG(prompt_token_count) AS avg_prompt_tok,
                   AVG(completion_token_count) AS avg_completion_tok
            FROM nemo.calls_extractions
            WHERE run_id = '{run_id}'
            GROUP BY extraction_status
        """

    if name == "compare_concurrency":
        test_name = _validate_simple_string(params["test_name"])
        return f"""
            SELECT concurrency,
                   AVG(req_per_sec) AS req_per_sec,
                   AVG(error_rate) AS error_rate,
                   AVG(prefix_cache_hit_ratio) AS hit_ratio,
                   AVG(gpu_util_mean) AS gpu_util
            FROM nemo.bench_measurements b
            JOIN nemo.experiment_runs r USING (run_id)
            WHERE r.test_name = '{test_name}'
            GROUP BY concurrency
            ORDER BY concurrency
        """

    if name == "failed_calls":
        run_id = _validate_run_id(params["run_id"])
        limit = int(params.get("limit", 100))
        return f"""
            SELECT call_id, error_message
            FROM nemo.calls_extractions
            WHERE run_id = '{run_id}' AND extraction_status = 'error'
            LIMIT {limit}
        """

    if name == "field_disagreement":
        run_a = _validate_run_id(params["run_a"])
        run_b = _validate_run_id(params["run_b"])
        field = _validate_simple_string(params["field"])
        return f"""
            SELECT a.call_id,
                   a.{field} AS run_a_value,
                   b.{field} AS run_b_value
            FROM (SELECT * FROM nemo.calls_extractions WHERE run_id = '{run_a}') a
            JOIN (SELECT * FROM nemo.calls_extractions WHERE run_id = '{run_b}') b
              USING (call_id)
            WHERE a.{field} IS DISTINCT FROM b.{field}
        """

    raise ValueError(f"unknown template: {name}")
