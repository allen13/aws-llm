"""Async runner — orchestrates client + writer."""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Iterable, Iterator, TYPE_CHECKING

from . import client as _client_module

if TYPE_CHECKING:
    from .config import ExtractConfig
    from .iceberg_writer import IcebergWriter


# Module-level event so the SIGTERM handler in extract_batch.py can flip it
# from outside the running coroutine. ``asyncio.Event`` is bound to a loop
# only when first awaited, so creating it eagerly is safe.
stop_after_current_event: asyncio.Event = asyncio.Event()


def _normalize_iterator(
    src: "Iterable[dict] | Iterator[dict] | AsyncIterator[dict]",
) -> AsyncIterator[dict]:
    """Coerce sync iterables into an async iterator."""
    if hasattr(src, "__aiter__"):
        return src  # type: ignore[return-value]

    async def _gen() -> AsyncIterator[dict]:
        for item in src:  # type: ignore[union-attr]
            yield item

    return _gen()


async def run(
    input_iter: "Iterable[dict] | AsyncIterator[dict]",
    cfg: "ExtractConfig",
    writer: "IcebergWriter",
    run_id: str,
    resume: bool = False,
) -> dict[str, int]:
    """Drive extraction over ``input_iter``.

    Each item must be a dict with ``call_id`` and ``transcript_text``
    (already truncated by the caller). Optional fields used to enrich
    the row: ``transcript_length_chars`` (original char count),
    ``was_truncated``, ``source_path``, ``category_path``, ``features``.

    Returns a small stats dict for the entrypoint to log.
    """
    skip_set: set[str] = set()
    if resume:
        skip_set = writer.query_skip_set(run_id)

    sem = asyncio.Semaphore(cfg.concurrency)
    in_flight: set[asyncio.Task[Any]] = set()
    stats = {"submitted": 0, "skipped": 0, "completed": 0, "errors": 0}

    async def _do_one(item: dict) -> None:
        call_id = item["call_id"]
        transcript = item.get("transcript_text") or item.get("transcript", "")
        try:
            async with sem:
                result = await _client_module.extract(
                    transcript=transcript,
                    call_id=call_id,
                    run_id=run_id,
                    cfg=cfg,
                    transcript_length_chars=item.get("transcript_length_chars"),
                    was_truncated=bool(item.get("was_truncated", False)),
                    source_path=item.get("source_path"),
                    category_path=item.get("category_path"),
                    features=item.get("features"),
                )
            writer.add_row(result)
            stats["completed"] += 1
            if result.extraction_status == "error":
                stats["errors"] += 1
        except Exception:
            stats["errors"] += 1
            raise

    async for item in _normalize_iterator(input_iter):
        if stop_after_current_event.is_set():
            break
        call_id = item["call_id"]
        if call_id in skip_set:
            stats["skipped"] += 1
            continue
        task = asyncio.create_task(_do_one(item))
        in_flight.add(task)
        task.add_done_callback(in_flight.discard)
        stats["submitted"] += 1

    if in_flight:
        await asyncio.gather(*in_flight, return_exceptions=True)

    writer.flush()
    return stats
