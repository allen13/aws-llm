"""Async OpenAI-compatible client for the vLLM server.

Output strategy: hand-written field-by-field SYSTEM_PROMPT (in
``schema.py``), then Pydantic ``Extraction.model_validate`` post-hoc.
We intentionally do **not** pass ``response_format``:

* ``response_format={"type":"json_schema"}``: vLLM 0.20 + xgrammar gets
  stuck in a whitespace loop on this nested ~50-field schema.
* ``response_format={"type":"json_object"}``: causes the model to keep
  emitting whitespace after the closing ``}`` until ``max_tokens`` is
  hit, producing truncated/unparseable output.

Both verified empirically on the local 3090 reference. The model stops
cleanly when given a clear plain-language spec.

Retries run on **both** network errors and parse/validation failures —
the model is non-deterministic and a second sample often validates.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any, Optional

# Lazy / defensive imports — the smoke test must pass without these
# packages installed on this dev machine.
try:  # pragma: no cover
    import httpx  # type: ignore
except Exception:  # pragma: no cover
    httpx = None  # type: ignore[assignment]

try:  # pragma: no cover
    import openai  # type: ignore
except Exception:  # pragma: no cover
    openai = None  # type: ignore[assignment]


from .schema import (
    CallExtraction,
    Extraction,
    SYSTEM_PROMPT,
    SCHEMA_VERSION,
    ValidationError,
)

if TYPE_CHECKING:
    from .config import ExtractConfig


_AsyncClient = None


def _get_client(cfg: "ExtractConfig"):
    """Lazily build a process-wide AsyncOpenAI client."""
    global _AsyncClient
    if _AsyncClient is not None:
        return _AsyncClient
    if openai is None:  # pragma: no cover
        raise RuntimeError(
            "openai package is not installed; install aws-llm-extract[dev] "
            "or `pip install openai>=1.50` before invoking extract()."
        )
    _AsyncClient = openai.AsyncOpenAI(
        base_url=f"{cfg.vllm_endpoint}/v1",
        api_key="EMPTY",
        timeout=cfg.request_timeout_s,
    )
    return _AsyncClient


def _retryable_network_excs() -> tuple[type[BaseException], ...]:
    excs: list[type[BaseException]] = [asyncio.TimeoutError]
    if httpx is not None:
        excs.append(httpx.HTTPError)
    if openai is not None:
        excs.append(openai.APIError)
    return tuple(excs)


_MAX_TOKENS = 1200  # Reference measured ~350-500 tok per response; 1200 is long-tail headroom.
_MAX_ATTEMPTS = 3
_BACKOFF_BASE_SEC = 2.0


async def extract(
    transcript: str,
    call_id: str,
    run_id: str,
    cfg: "ExtractConfig",
    *,
    transcript_length_chars: Optional[int] = None,
    was_truncated: bool = False,
    source_path: Optional[str] = None,
    category_path: Optional[str] = None,
    features: Optional[dict] = None,
) -> CallExtraction:
    """Run one extraction call against vLLM and return a ``CallExtraction``.

    ``transcript`` is expected to be already-truncated; the caller
    (``extract_batch.py``) is responsible for head+tail truncation so
    ``transcript_length_chars`` reflects the *original* call length. If
    it isn't passed in, fall back to ``len(transcript)``.

    Retries up to 3 times. On every attempt we try the HTTP call, parse,
    and Pydantic-validate; any failure path is retried (with exponential
    backoff) until attempts are exhausted, after which the last error is
    captured on a status="error" row.
    """
    if transcript_length_chars is None:
        transcript_length_chars = len(transcript)

    common_kwargs: dict[str, Any] = dict(
        call_id=call_id,
        run_id=run_id,
        source_path=source_path,
        category_path=category_path,
        transcript_text=transcript,
        features=features,
        model_id=cfg.model_name,
        schema_version=SCHEMA_VERSION,
        transcript_length_chars=transcript_length_chars,
        was_truncated=was_truncated,
    )

    network_excs = _retryable_network_excs()
    last_error: str = "exhausted retries"
    last_prompt_tokens: Optional[int] = None
    last_completion_tokens: Optional[int] = None

    for attempt in range(_MAX_ATTEMPTS):
        try:
            client = _get_client(cfg)
            resp = await client.chat.completions.create(
                model=cfg.model_name,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"TRANSCRIPT:\n{transcript}"},
                ],
                extra_body={
                    # Per Nemotron docs — toggles the ``nano_v3`` reasoning parser.
                    "chat_template_kwargs": {"enable_thinking": cfg.enable_thinking},
                },
                max_tokens=_MAX_TOKENS,
                temperature=0.2,
                top_p=0.9,
            )
        except network_excs as e:
            last_error = f"api_error: {type(e).__name__}: {e}"
            await _sleep_backoff(attempt)
            continue
        except Exception as e:  # pragma: no cover — unexpected transport failure
            last_error = f"unexpected_error: {type(e).__name__}: {e}"
            await _sleep_backoff(attempt)
            continue

        usage = getattr(resp, "usage", None)
        if usage is not None:
            last_prompt_tokens = getattr(usage, "prompt_tokens", None)
            last_completion_tokens = getattr(usage, "completion_tokens", None)

        try:
            content = resp.choices[0].message.content or ""
        except (AttributeError, IndexError, TypeError) as e:
            last_error = f"missing_content: {type(e).__name__}: {e}"
            await _sleep_backoff(attempt)
            continue

        try:
            payload = json.loads(content)
        except (json.JSONDecodeError, ValueError) as e:
            last_error = f"json_parse_error: {type(e).__name__}: {str(e)[:300]}"
            await _sleep_backoff(attempt)
            continue

        try:
            extraction = Extraction.model_validate(payload)
        except ValidationError as e:
            last_error = f"validation_error: {str(e)[:500]}"
            await _sleep_backoff(attempt)
            continue
        except Exception as e:  # pragma: no cover — defensive
            last_error = f"validation_error: {type(e).__name__}: {str(e)[:300]}"
            await _sleep_backoff(attempt)
            continue

        return CallExtraction(
            extraction=extraction,
            extraction_status="success",
            error_message=None,
            prompt_token_count=last_prompt_tokens,
            completion_token_count=last_completion_tokens,
            **common_kwargs,
        )

    return CallExtraction(
        extraction=None,
        extraction_status="error",
        error_message=last_error,
        prompt_token_count=last_prompt_tokens,
        completion_token_count=last_completion_tokens,
        **common_kwargs,
    )


async def _sleep_backoff(attempt: int) -> None:
    if attempt + 1 >= _MAX_ATTEMPTS:
        return
    await asyncio.sleep(_BACKOFF_BASE_SEC * (2**attempt))
