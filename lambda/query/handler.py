"""Athena-via-Lambda query handler for the aws-llm project.

Receives a SQL string or template invocation, runs it through Athena's
``aws-llm`` workgroup, polls for completion, and returns paginated results
shaped for downstream tooling.

The Lambda's IAM role is read-only (SELECT-only at the workgroup); we still
defense-in-depth verify the first non-comment word is SELECT or WITH so a
mistakenly-elevated role (or a future template bug) can't slip writes through.
"""

from __future__ import annotations

import json
import os
import re
import time

import boto3

import templates


_MAX_POLL_SECONDS = 50
_POLL_INTERVAL_SECONDS = 1


def _strip_leading_comments(sql: str) -> str:
    """Drop leading SQL comments and whitespace so the read-only check sees the
    first real keyword."""
    s = sql.lstrip()
    while True:
        if s.startswith("--"):
            nl = s.find("\n")
            s = "" if nl == -1 else s[nl + 1 :]
            s = s.lstrip()
        elif s.startswith("/*"):
            end = s.find("*/")
            s = "" if end == -1 else s[end + 2 :]
            s = s.lstrip()
        else:
            return s


def _is_read_only(sql: str) -> bool:
    head = _strip_leading_comments(sql)
    m = re.match(r"([A-Za-z]+)", head)
    if not m:
        return False
    return m.group(1).upper() in {"SELECT", "WITH"}


def _build_response(status_code: int, body: dict) -> dict:
    return {"statusCode": status_code, "body": json.dumps(body)}


def _shape_results(athena, qid: str) -> dict:
    """Page through GetQueryResults and return a flat columns/rows dict."""
    paginator = athena.get_paginator("get_query_results")
    columns: list[str] = []
    rows: list[list] = []
    first = True
    for page in paginator.paginate(QueryExecutionId=qid):
        result_set = page["ResultSet"]
        if first:
            col_info = result_set.get("ResultSetMetadata", {}).get("ColumnInfo", [])
            columns = [c["Name"] for c in col_info]
            page_rows = result_set.get("Rows", [])
            # Athena's first row is the column header; skip it.
            page_rows = page_rows[1:] if page_rows else []
            first = False
        else:
            page_rows = result_set.get("Rows", [])
        for r in page_rows:
            rows.append([d.get("VarCharValue") for d in r.get("Data", [])])
    return {"columns": columns, "rows": rows}


def handler(event, context):  # noqa: ARG001 - context is required by Lambda
    workgroup = os.environ.get("ATHENA_WORKGROUP")
    database = os.environ.get("ATHENA_DATABASE", "nemo")
    output_location = os.environ.get("ATHENA_OUTPUT_LOCATION")

    if not workgroup or not output_location:
        return _build_response(
            500,
            {
                "error": "missing required env vars: "
                "ATHENA_WORKGROUP, ATHENA_OUTPUT_LOCATION",
            },
        )

    # Resolve SQL — either from a template or a raw `sql` field.
    try:
        if event.get("template"):
            sql = templates.render(event["template"], event.get("params", {}) or {})
        elif event.get("sql"):
            sql = event["sql"]
        else:
            return _build_response(
                400, {"error": "event must include `template` or `sql`"}
            )
    except (KeyError, ValueError) as e:
        return _build_response(400, {"error": f"template error: {e}"})

    if not _is_read_only(sql):
        return _build_response(
            400,
            {"error": "only SELECT/WITH queries are accepted by this Lambda"},
        )

    athena = boto3.client("athena")

    try:
        start = athena.start_query_execution(
            QueryString=sql,
            QueryExecutionContext={"Database": database},
            WorkGroup=workgroup,
            ResultConfiguration={"OutputLocation": output_location},
        )
    except Exception as e:  # noqa: BLE001
        return _build_response(500, {"error": f"start_query_execution failed: {e}"})

    qid = start["QueryExecutionId"]

    deadline = time.time() + _MAX_POLL_SECONDS
    state = "QUEUED"
    info = None
    while time.time() < deadline:
        info = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]
        state = info["Status"]["State"]
        if state in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            break
        time.sleep(_POLL_INTERVAL_SECONDS)
    else:
        return _build_response(
            504,
            {
                "error": "query timed out within Lambda poll window",
                "queryExecutionId": qid,
                "state": state,
            },
        )

    stats = (info or {}).get("Statistics", {}) or {}
    bytes_scanned = stats.get("DataScannedInBytes", 0)
    runtime_ms = stats.get("EngineExecutionTimeInMillis", 0)

    if state != "SUCCEEDED":
        reason = (info or {}).get("Status", {}).get("StateChangeReason", "")
        return _build_response(
            400,
            {
                "error": f"query {state}: {reason}",
                "queryExecutionId": qid,
                "bytesScanned": bytes_scanned,
                "runtimeMs": runtime_ms,
            },
        )

    try:
        shaped = _shape_results(athena, qid)
    except Exception as e:  # noqa: BLE001
        return _build_response(500, {"error": f"get_query_results failed: {e}"})

    shaped["bytesScanned"] = bytes_scanned
    shaped["runtimeMs"] = runtime_ms
    shaped["queryExecutionId"] = qid

    return _build_response(200, shaped)
