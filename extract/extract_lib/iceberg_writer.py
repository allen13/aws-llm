"""PyIceberg writer for the three S3 Tables.

Imports of ``pyiceberg`` are deferred into method bodies so the smoke
test (which has no Iceberg catalog reachable) can ``import`` this
module without contacting AWS.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Iterable, Literal, TYPE_CHECKING

from .schema import (
    CallExtraction,
    BENCH_MEASUREMENT_FIELDS,
    EXPERIMENT_RUNS_FIELDS,
    get_arrow_schema,
)

if TYPE_CHECKING:
    from .config import ExtractConfig


CALLS_EXTRACTIONS_TABLE = "calls_extractions"
BENCH_MEASUREMENTS_TABLE = "bench_measurements"
EXPERIMENT_RUNS_TABLE = "experiment_runs"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp_field_ids(arrow_schema):
    """Walk a pyarrow Schema and assign sequential ``PARQUET:field_id``
    metadata to every field (including struct children).

    pyiceberg 0.11+ requires field-ids on either the pyarrow schema or
    via a NameMapping; absent both, ``pyarrow_to_schema`` raises
    ValueError("Parquet file does not have field-ids and the Iceberg
    table does not have 'schema.name-mapping.default' defined").
    Sequential traversal order is fine: the IDs only need to be unique
    and stable on first table create — pyiceberg stores them in the
    catalog and uses those for all subsequent reads/writes.
    """
    import pyarrow as pa  # type: ignore

    counter = [0]

    def _next_id() -> bytes:
        counter[0] += 1
        return str(counter[0]).encode()

    def _stamp(field: "pa.Field") -> "pa.Field":
        meta = dict(field.metadata or {})
        meta[b"PARQUET:field_id"] = _next_id()
        if pa.types.is_struct(field.type):
            new_children = [_stamp(field.type.field(i)) for i in range(field.type.num_fields)]
            new_type = pa.struct(new_children)
            return pa.field(field.name, new_type, nullable=field.nullable, metadata=meta)
        if pa.types.is_list(field.type):
            child = _stamp(field.type.value_field)
            new_type = pa.list_(child)
            return pa.field(field.name, new_type, nullable=field.nullable, metadata=meta)
        if pa.types.is_map(field.type):
            key = _stamp(field.type.key_field)
            val = _stamp(field.type.item_field)
            new_type = pa.map_(key.type, val.type)
            return pa.field(field.name, new_type, nullable=field.nullable, metadata=meta)
        return pa.field(field.name, field.type, nullable=field.nullable, metadata=meta)

    return pa.schema([_stamp(field) for field in arrow_schema])


def _sibling_arrow_schema(table_name: str):
    """Build an explicit pyarrow schema for the sibling tables.

    Returns ``None`` for tables that should fall back to Arrow inference
    (i.e. ``calls_extractions``, which has its own nested schema in
    ``schema.py``).

    Why explicit: Arrow infers ``pa.null()`` for columns whose first row
    is ``None`` (e.g. ``ended_at`` while a run is still running). Iceberg
    format-version 2 rejects ``null`` types — see ``start_experiment_run``
    seed row. The dicts in ``schema.py`` are the source of truth for
    these tables; we map them to concrete Arrow types here.
    """
    import pyarrow as pa  # type: ignore

    type_map = {
        "string": pa.string(),
        "int": pa.int64(),
        "double": pa.float64(),
        "boolean": pa.bool_(),
        "timestamp": pa.timestamp("us", tz="UTC"),
    }

    if table_name == EXPERIMENT_RUNS_TABLE:
        fields = EXPERIMENT_RUNS_FIELDS
    elif table_name == BENCH_MEASUREMENTS_TABLE:
        fields = BENCH_MEASUREMENT_FIELDS
    else:
        return None

    return pa.schema([(k, type_map[v]) for k, v in fields.items()])


class IcebergWriter:
    """Buffered PyIceberg writer.

    Use ``mode="local"`` to construct a no-op writer for tests; this avoids
    any network calls and any pyiceberg import.
    """

    def __init__(
        self,
        cfg: "ExtractConfig",
        run_id: str,
        mode: Literal["catalog", "local"] = "catalog",
    ) -> None:
        self.cfg = cfg
        self.run_id = run_id
        self.mode = mode
        self._buffer: list[CallExtraction] = []
        self._last_flush_ts = time.monotonic()
        self._catalog: Any | None = None
        self._tables: dict[str, Any] = {}

        if mode == "catalog":
            # Defer the pyiceberg import — keeps `import extract_lib` cheap
            # and network-free on dev machines without pyiceberg installed.
            self._init_catalog()

    # ------------------------------------------------------------------
    # Catalog plumbing
    # ------------------------------------------------------------------

    def _init_catalog(self) -> None:
        try:
            from pyiceberg.catalog import load_catalog  # type: ignore
        except Exception as e:  # pragma: no cover
            raise RuntimeError(
                "pyiceberg is required for catalog mode; install "
                "`pyiceberg[pyarrow]>=0.7` or construct with mode='local'."
            ) from e

        # S3 Tables exposes an Iceberg REST catalog at this regional URL.
        # The warehouse is the table bucket ARN (passed via env or config).
        # SigV4 signing uses service name "s3tables" — required as of 2025+.
        if not self.cfg.tables_bucket_arn:
            raise RuntimeError(
                "TABLES_BUCKET_ARN is not set; pass via env or ExtractConfig. "
                "Expected shape: arn:aws:s3tables:<region>:<account>:bucket/<name>"
            )
        self._catalog = load_catalog(
            "nemo",
            **{
                "type": "rest",
                "uri": f"https://s3tables.{self.cfg.aws_region}.amazonaws.com/iceberg",
                "warehouse": self.cfg.tables_bucket_arn,
                "rest.sigv4-enabled": "true",
                "rest.signing-region": self.cfg.aws_region,
                "rest.signing-name": "s3tables",
            },
        )

    def _table(self, name: str) -> Any:
        if self.mode == "local":
            raise RuntimeError("local mode does not have backing tables")
        if name in self._tables:
            return self._tables[name]
        if self._catalog is None:
            self._init_catalog()
        identifier = (self.cfg.tables_namespace, name)
        try:
            tbl = self._catalog.load_table(identifier)  # type: ignore[union-attr]
        except Exception:
            tbl = self._create_table(name, identifier)
        self._tables[name] = tbl
        return tbl

    def _create_table(self, name: str, identifier: tuple[str, str]) -> Any:
        """Create the Iceberg table on first access if it doesn't exist.

        For ``calls_extractions`` we use the full nested struct schema
        from ``schema.py``. Sibling tables fall back to PyIceberg's
        Arrow-inferred schema from a single seed row.
        """
        from pyiceberg.catalog import Catalog  # type: ignore  # noqa: F401
        from pyiceberg.io.pyarrow import pyarrow_to_schema  # type: ignore

        if name == CALLS_EXTRACTIONS_TABLE:
            arrow_schema = _stamp_field_ids(get_arrow_schema())
            iceberg_schema = pyarrow_to_schema(arrow_schema)
            return self._catalog.create_table(  # type: ignore[union-attr]
                identifier=identifier,
                schema=iceberg_schema,
            )
        # bench_measurements / experiment_runs: caller must seed via
        # _append_one() which infers from the first row's Arrow types.
        raise RuntimeError(
            f"table {identifier} not found; sibling tables are created "
            f"on first append by _append_one()"
        )

    # ------------------------------------------------------------------
    # calls_extractions — buffered append
    # ------------------------------------------------------------------

    def add_row(self, row: CallExtraction) -> None:
        self._buffer.append(row)
        elapsed = time.monotonic() - self._last_flush_ts
        if (
            len(self._buffer) >= self.cfg.commit_every_n_rows
            or elapsed >= self.cfg.commit_every_n_seconds
        ):
            self.flush()

    def flush(self) -> None:
        if not self._buffer:
            self._last_flush_ts = time.monotonic()
            return

        rows = [r.model_dump() if hasattr(r, "model_dump") else dict(r.__dict__)
                for r in self._buffer]

        if self.mode == "local":
            # Drop the buffer; the local mode is for unit tests.
            self._buffer.clear()
            self._last_flush_ts = time.monotonic()
            return

        try:
            import pyarrow as pa  # type: ignore
        except Exception as e:  # pragma: no cover
            raise RuntimeError(
                "pyarrow is required to write Iceberg tables"
            ) from e

        tbl = self._table(CALLS_EXTRACTIONS_TABLE)
        # Pin the schema so PyIceberg lands stable struct types instead
        # of inferring from the buffer (different null patterns across
        # batches would otherwise drift the inferred schema).
        arrow_table = pa.Table.from_pylist(rows, schema=get_arrow_schema())
        tbl.append(arrow_table)  # atomic Iceberg commit

        self._buffer.clear()
        self._last_flush_ts = time.monotonic()

    # ------------------------------------------------------------------
    # Resume support
    # ------------------------------------------------------------------

    def query_skip_set(self, run_id: str) -> set[str]:
        """Return the set of ``call_id``s already committed as ``success``
        for ``run_id``. Used by ``runner.run(..., resume=True)``.
        """
        if self.mode == "local":
            return set()
        tbl = self._table(CALLS_EXTRACTIONS_TABLE)
        try:
            scan = tbl.scan(
                row_filter=(
                    f"run_id = '{run_id}' AND extraction_status = 'success'"
                ),
                selected_fields=("call_id",),
            )
            arrow = scan.to_arrow()
            return set(arrow.column("call_id").to_pylist())
        except Exception:  # pragma: no cover — table may not exist yet
            return set()

    # ------------------------------------------------------------------
    # bench_measurements
    # ------------------------------------------------------------------

    def record_bench_measurement(
        self,
        *,
        run_id: str,
        concurrency: int,
        req_per_sec: float,
        error_rate: float,
        prefix_cache_hit_ratio: float,
        gpu_util_mean: float,
        total_calls: int,
        duration_seconds: float,
        measured_at: datetime | None = None,
    ) -> None:
        # Schema field list comes from BENCH_MEASUREMENT_FIELDS — assert we're
        # not silently drifting from PLAN.md.
        assert set(BENCH_MEASUREMENT_FIELDS.keys()) >= {
            "run_id", "concurrency", "req_per_sec", "error_rate",
            "prefix_cache_hit_ratio", "gpu_util_mean", "total_calls",
            "duration_seconds", "measured_at",
        }
        row = {
            "run_id": run_id,
            "concurrency": concurrency,
            "req_per_sec": req_per_sec,
            "error_rate": error_rate,
            "prefix_cache_hit_ratio": prefix_cache_hit_ratio,
            "gpu_util_mean": gpu_util_mean,
            "total_calls": total_calls,
            "duration_seconds": duration_seconds,
            "measured_at": measured_at or _now(),
        }
        self._append_one(BENCH_MEASUREMENTS_TABLE, row)

    # ------------------------------------------------------------------
    # experiment_runs
    # ------------------------------------------------------------------

    def start_experiment_run(
        self,
        run_id: str,
        test_name: str,
        git_sha: str,
        model_manifest_hash: str,
        vllm_image_uri: str,
        instance_type: str,
        use_spot: bool,
        region: str,
        enable_thinking: bool,
        max_model_len: int,
        max_num_seqs: int,
        notes: str = "",
    ) -> None:
        row = {
            "run_id": run_id,
            "test_name": test_name,
            "git_sha": git_sha,
            "model_manifest_hash": model_manifest_hash,
            "vllm_image_uri": vllm_image_uri,
            "instance_type": instance_type,
            "use_spot": use_spot,
            "region": region,
            "enable_thinking": enable_thinking,
            "max_model_len": max_model_len,
            "max_num_seqs": max_num_seqs,
            "started_at": _now(),
            "ended_at": None,
            "status": "running",
            "notes": notes,
        }
        # Schema list-of-keys is enforced by EXPERIMENT_RUNS_FIELDS.
        assert set(row.keys()) == set(EXPERIMENT_RUNS_FIELDS.keys())
        self._append_one(EXPERIMENT_RUNS_TABLE, row)

    def finish_experiment_run(
        self,
        run_id: str,
        status: Literal["success", "partial", "failed", "interrupted"],
    ) -> None:
        """Mark an experiment run finished.

        Iceberg is append-only, so this is implemented as a copy-on-write
        UPDATE: read-modify-write through PyIceberg's ``overwrite`` API
        scoped to a single ``run_id``.
        """
        if self.mode == "local":
            return
        try:
            import pyarrow as pa  # type: ignore
            from pyiceberg.expressions import EqualTo  # type: ignore
        except Exception as e:  # pragma: no cover
            raise RuntimeError("pyiceberg + pyarrow required") from e

        tbl = self._table(EXPERIMENT_RUNS_TABLE)
        existing = tbl.scan(row_filter=EqualTo("run_id", run_id)).to_arrow()
        if existing.num_rows == 0:  # pragma: no cover
            return
        rows = existing.to_pylist()
        for r in rows:
            r["status"] = status
            r["ended_at"] = _now()
        new_table = pa.Table.from_pylist(rows)
        tbl.overwrite(new_table, overwrite_filter=EqualTo("run_id", run_id))

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _append_one(self, table_name: str, row: dict[str, Any]) -> None:
        if self.mode == "local":
            return
        try:
            import pyarrow as pa  # type: ignore
        except Exception as e:  # pragma: no cover
            raise RuntimeError("pyarrow required") from e
        # Prefer explicit schema for sibling tables so ``ended_at=None``
        # in the seed row doesn't get inferred as pa.null() (rejected by
        # Iceberg format-version 2). For tables without an explicit
        # schema, fall back to row inference.
        explicit = _sibling_arrow_schema(table_name)
        if explicit is not None:
            append_tbl = pa.Table.from_pylist([row], schema=explicit)
        else:
            append_tbl = pa.Table.from_pylist([row])

        if table_name in self._tables:
            tbl = self._tables[table_name]
        else:
            identifier = (self.cfg.tables_namespace, table_name)
            try:
                tbl = self._catalog.load_table(identifier)  # type: ignore[union-attr]
            except Exception:
                tbl = self._catalog.create_table(  # type: ignore[union-attr]
                    identifier=identifier,
                    schema=append_tbl.schema,
                )
            self._tables[table_name] = tbl
        tbl.append(append_tbl)

    def append_many(
        self, table_name: str, rows: Iterable[dict[str, Any]]
    ) -> None:
        if self.mode == "local":
            return
        try:
            import pyarrow as pa  # type: ignore
        except Exception as e:  # pragma: no cover
            raise RuntimeError("pyarrow required") from e
        tbl = self._table(table_name)
        tbl.append(pa.Table.from_pylist(list(rows)))
