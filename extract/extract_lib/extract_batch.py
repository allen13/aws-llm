"""Entrypoint for the extraction pipeline.

Usage::

    aws-llm-extract --run-id <id> --input calls.jsonl
    aws-llm-extract --run-id <id> --input calls.jsonl --resume

Wired up via ``[project.scripts]`` in ``pyproject.toml``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Iterator

from extract_lib import runner as runner_module
from extract_lib.config import ExtractConfig
from extract_lib.features import compute_features, compute_features_from_text
from extract_lib.iceberg_writer import IcebergWriter


_TRUNC_MARKER = "\n\n[...transcript middle truncated for length...]\n\n"


def _truncate(text: str, max_chars: int) -> tuple[str, int, bool]:
    """Head+tail truncation matching the reference.

    Keeps the call's opening (greeting / intent) and closing (resolution
    / signoff) — the parts most informative for QA — and drops the
    middle. Required because long transcripts (>10k chars) returned
    empty model responses under load on the reference.

    Returns ``(text_to_send, original_length, was_truncated)``.
    """
    original = len(text)
    if not max_chars or original <= max_chars:
        return text, original, False
    budget = max_chars - len(_TRUNC_MARKER)
    head_len = budget // 2
    tail_len = budget - head_len
    return text[:head_len] + _TRUNC_MARKER + text[-tail_len:], original, True


def _enrich_item(raw: dict, max_chars: int) -> dict:
    """Augment a JSONL row with truncation telemetry and features.

    Accepts inputs in two shapes:
    1. ``{"call_id": ..., "transcript": "<text>", ...}`` — bare text.
    2. ``{"call_id": ..., "transcript": {"text": ..., "words": [...],
       "audio_duration": ..., "confidence": ...}, ...}`` — full AIxBlock
       transcript dict (yields full 14-field features).
    """
    call_id = raw["call_id"]
    raw_transcript = raw.get("transcript", "")

    if isinstance(raw_transcript, dict):
        transcript_text_full = raw_transcript.get("text", "") or ""
        features = compute_features(raw_transcript)
    else:
        transcript_text_full = raw_transcript or ""
        features = compute_features_from_text(transcript_text_full)

    send_text, original_chars, was_trunc = _truncate(transcript_text_full, max_chars)

    return {
        "call_id": call_id,
        "transcript_text": send_text,
        "transcript_length_chars": original_chars,
        "was_truncated": was_trunc,
        "features": features,
        "source_path": raw.get("source_path"),
        "category_path": raw.get("category_path"),
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="aws-llm-extract")
    p.add_argument("--run-id", required=True, help="Experiment run id")
    p.add_argument(
        "--input",
        required=True,
        type=Path,
        help="JSONL file with {call_id, transcript} per line",
    )
    p.add_argument("--concurrency", type=int, default=None)
    p.add_argument("--max-transcript-chars", type=int, default=None)
    p.add_argument(
        "--resume",
        action="store_true",
        help="Skip call_ids already committed under this run_id",
    )
    p.add_argument("--test-name", default="TestUnknown")
    p.add_argument(
        "--writer-mode",
        choices=("catalog", "local"),
        default="catalog",
        help="`local` is for tests; skips Iceberg I/O entirely",
    )
    return p.parse_args(argv)


def _iter_jsonl(path: Path, max_transcript_chars: int) -> Iterator[dict]:
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield _enrich_item(json.loads(line), max_transcript_chars)


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip()
    except Exception:
        return "unknown"


def _write_pidfile(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        f.write(str(os.getpid()))


def _install_sigterm_handler(writer: IcebergWriter) -> None:
    def _handler(signum, frame):  # noqa: ARG001
        # 1. Stop dispatching new tasks.
        runner_module.stop_after_current_event.set()
        # 2. Flush whatever is buffered. The runner.run() coroutine will
        #    also flush on exit; this is the belt-and-suspenders path
        #    for the rare case where the loop is wedged.
        try:
            writer.flush()
        except Exception as e:  # pragma: no cover
            print(f"[extract_batch] flush on SIGTERM failed: {e}", file=sys.stderr)

    signal.signal(signal.SIGTERM, _handler)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    cfg = ExtractConfig()
    if args.concurrency is not None:
        cfg.concurrency = args.concurrency
    if args.max_transcript_chars is not None:
        cfg.max_transcript_chars = args.max_transcript_chars

    _write_pidfile(cfg.pidfile_path)

    writer = IcebergWriter(cfg, run_id=args.run_id, mode=args.writer_mode)
    _install_sigterm_handler(writer)

    if not args.resume:
        writer.start_experiment_run(
            run_id=args.run_id,
            test_name=args.test_name,
            git_sha=_git_sha(),
            model_manifest_hash=os.environ.get("MODEL_MANIFEST_HASH", ""),
            vllm_image_uri=os.environ.get("VLLM_IMAGE_URI", ""),
            instance_type=os.environ.get("INSTANCE_TYPE", "g6e.xlarge"),
            use_spot=os.environ.get("USE_SPOT", "false").lower() == "true",
            region=cfg.aws_region,
            enable_thinking=cfg.enable_thinking,
            max_model_len=int(os.environ.get("MAX_MODEL_LEN", "8192")),
            max_num_seqs=int(os.environ.get("MAX_NUM_SEQS", "32")),
        )

    final_status = "success"
    try:
        stats = asyncio.run(
            runner_module.run(
                input_iter=_iter_jsonl(args.input, cfg.max_transcript_chars),
                cfg=cfg,
                writer=writer,
                run_id=args.run_id,
                resume=args.resume,
            )
        )
        if stats.get("errors", 0) > 0 and stats.get("completed", 0) == stats.get("errors", 0):
            final_status = "failed"
        elif stats.get("errors", 0) > 0:
            final_status = "partial"
        print(f"[extract_batch] stats={stats}", file=sys.stderr)
    except KeyboardInterrupt:
        final_status = "interrupted"
    except Exception as e:
        print(f"[extract_batch] run failed: {e}", file=sys.stderr)
        final_status = "failed"
    finally:
        try:
            writer.finish_experiment_run(args.run_id, status=final_status)  # type: ignore[arg-type]
        except Exception as e:
            print(f"[extract_batch] finish_experiment_run failed: {e}", file=sys.stderr)

    return 0 if final_status == "success" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
