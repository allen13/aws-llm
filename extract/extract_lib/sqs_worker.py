"""SQS-driven batch worker for the aws-llm runtime instance.

Runs as a systemd unit (aws-llm-sqs-worker.service) installed by
``infra/runtime/bootstrap.sh`` step 6, after vLLM is healthy. The unit is
ordered After=aws-llm-bootstrap.service and gated on the existence of
``/var/log/aws-llm/bootstrap.done``.

Loop semantics
--------------
1. Read instance launch-time + instance-id from IMDSv2.
2. Long-poll SQS for a message (wait_time=20s).
3. If empty AND idle ≥ ``IDLE_TERMINATE_AFTER_S``: self-terminate via the
   ASG.
4. If wall-clock since launch ≥ ``WALL_CLOCK_TERMINATE_AFTER_S``: refuse to
   start a new shard, finish in-flight, self-terminate.
5. Parse the S3 event JSON; download the referenced object to
   ``/tmp/<basename>``; gunzip if needed.
6. Derive a run_id from the S3 key (so retries with the same key share the
   same Iceberg run, and ``--resume`` correctly skips already-extracted
   call_ids).
7. Invoke ``extract_lib.extract_batch.main()`` with ``--resume`` so a
   partially-processed shard recovers cleanly.
8. On clean exit (return 0): DeleteMessage. On non-zero or exception: leave
   the message visible (visibility-timeout heartbeat keeps it owned by us
   until the shard completes or the wall-clock cap forces termination).

Environment (read from ``/etc/environment`` via the systemd unit):
    JOBS_QUEUE_URL          required, SQS queue URL
    ASG_NAME                required, runtime ASG name (for self-terminate)
    AWS_REGION              required, used by boto3 clients
    WORKER_WALL_CAP_SEC     optional, default 3000 (50 min)
    WORKER_IDLE_CAP_SEC     optional, default 300  (5 min)
    WORKER_VIS_HEARTBEAT_S  optional, default 240  (4 min)
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import re
import shutil
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Optional
from urllib.parse import unquote_plus

import boto3
import urllib.request
import urllib.error

logger = logging.getLogger("aws_llm.sqs_worker")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)


_IMDS_TOKEN_URL = "http://169.254.169.254/latest/api/token"
_IMDS_META_URL = "http://169.254.169.254/latest/meta-data"
_IMDS_DOC_URL = "http://169.254.169.254/latest/dynamic/instance-identity/document"


def _imds_token(ttl: int = 21600) -> str:
    req = urllib.request.Request(
        _IMDS_TOKEN_URL,
        method="PUT",
        headers={"X-aws-ec2-metadata-token-ttl-seconds": str(ttl)},
    )
    with urllib.request.urlopen(req, timeout=2) as resp:
        return resp.read().decode("utf-8")


def _imds_get(path: str, token: str) -> str:
    req = urllib.request.Request(
        f"{_IMDS_META_URL}/{path}" if not path.startswith("http") else path,
        headers={"X-aws-ec2-metadata-token": token},
    )
    with urllib.request.urlopen(req, timeout=2) as resp:
        return resp.read().decode("utf-8")


def _read_imds() -> dict:
    """Return {'instance_id', 'launch_time_unix', 'region'} from IMDSv2."""
    token = _imds_token()
    instance_id = _imds_get("instance-id", token)
    # The instance-identity document carries launch-time as ISO8601.
    req = urllib.request.Request(
        _IMDS_DOC_URL, headers={"X-aws-ec2-metadata-token": token}
    )
    with urllib.request.urlopen(req, timeout=2) as resp:
        doc = json.loads(resp.read().decode("utf-8"))
    # 'pendingTime' is the canonical "instance came into existence" timestamp;
    # imageId / instanceType etc. are also available here.
    pending = doc["pendingTime"]  # e.g. '2026-05-02T01:23:45Z'
    pending_unix = time.mktime(time.strptime(pending, "%Y-%m-%dT%H:%M:%SZ"))
    return {
        "instance_id": instance_id,
        "launch_time_unix": pending_unix,
        "region": doc.get("region") or os.environ.get("AWS_REGION", "us-east-1"),
    }


# ----------------------------------------------------------------------------
# Visibility-timeout heartbeat
# ----------------------------------------------------------------------------

class VisibilityHeartbeat:
    """Background thread that periodically extends a single in-flight
    SQS message's visibility timeout, so a long-running ``extract_batch``
    doesn't lose ownership and trigger SQS redelivery.

    Use as a context manager around the call to ``extract_batch.main()``.
    """

    def __init__(
        self,
        sqs_client,
        queue_url: str,
        receipt_handle: str,
        interval_s: int,
        extend_to_s: int,
    ):
        self._sqs = sqs_client
        self._queue_url = queue_url
        self._receipt_handle = receipt_handle
        self._interval_s = interval_s
        self._extend_to_s = extend_to_s
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _run(self):
        while not self._stop.wait(self._interval_s):
            try:
                self._sqs.change_message_visibility(
                    QueueUrl=self._queue_url,
                    ReceiptHandle=self._receipt_handle,
                    VisibilityTimeout=self._extend_to_s,
                )
                logger.debug("extended visibility by %ds", self._extend_to_s)
            except Exception as e:  # noqa: BLE001
                logger.warning("visibility heartbeat failed: %s", e)
                # Don't break — transient AWS API errors shouldn't kill the
                # heartbeat. The next tick will retry.

    def __enter__(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)


# ----------------------------------------------------------------------------
# S3 event parsing
# ----------------------------------------------------------------------------

_RUN_ID_SAFE = re.compile(r"[^A-Za-z0-9_-]+")


class _ParseResult:
    """Discriminated result type for SQS message parsing."""
    OBJECT = "object"        # real S3 ObjectCreated event with (bucket, key)
    TEST_EVENT = "test"      # S3's reachability ping; safe to delete
    UNKNOWN = "unknown"      # malformed or unexpected; let SQS retry → DLQ


def _parse_s3_event(body: str) -> tuple[str, Optional[tuple[str, str]]]:
    """Parse an SQS message body that should be an S3 notification.

    Returns ``(kind, payload)`` where ``kind`` is one of ``OBJECT``,
    ``TEST_EVENT``, ``UNKNOWN``. ``payload`` is ``(bucket, key)`` only when
    ``kind == OBJECT``; ``None`` otherwise.

    S3's TestEvent is sent automatically when a bucket notification is first
    created or modified, to verify the destination is reachable. We recognize
    and delete it rather than letting it cycle to the DLQ.
    """
    try:
        envelope = json.loads(body)
    except json.JSONDecodeError:
        logger.warning("message body is not JSON: %r", body[:200])
        return _ParseResult.UNKNOWN, None

    # SNS-wrapped delivery would be `{"Type":"Notification","Message":"..."}`.
    # We don't subscribe S3 → SNS → SQS in this stack, but unwrap defensively.
    if isinstance(envelope, dict) and envelope.get("Type") == "Notification":
        try:
            envelope = json.loads(envelope["Message"])
        except (KeyError, json.JSONDecodeError):
            logger.warning("could not unwrap SNS-wrapped envelope")
            return _ParseResult.UNKNOWN, None

    if not isinstance(envelope, dict):
        return _ParseResult.UNKNOWN, None

    # S3:TestEvent is the reachability ping. No Records[]; has Service=Amazon S3
    # and Event=s3:TestEvent. Cf. https://docs.aws.amazon.com/AmazonS3/latest/userguide/notification-content-structure.html
    if envelope.get("Event") == "s3:TestEvent" and envelope.get("Service") == "Amazon S3":
        return _ParseResult.TEST_EVENT, None

    records = envelope.get("Records")
    if not records:
        return _ParseResult.UNKNOWN, None

    record = records[0]
    s3 = record.get("s3") or {}
    bucket = (s3.get("bucket") or {}).get("name")
    key = (s3.get("object") or {}).get("key")
    if not bucket or not key:
        return _ParseResult.UNKNOWN, None
    # S3 URL-encodes plus-as-space and special chars in keys.
    key = unquote_plus(key)
    return _ParseResult.OBJECT, (bucket, key)


def _derive_run_id(s3_key: str) -> str:
    """Stable run_id derived from the S3 key.

    Same key → same run_id, so SQS redelivery + ``--resume`` correctly skip
    already-extracted call_ids. We strip the prefix and the .jsonl.gz suffix
    and replace any non-portable characters with '-'.
    """
    base = Path(s3_key).name
    base = base.removesuffix(".gz")
    base = base.removesuffix(".jsonl")
    base = base.removesuffix(".json")
    return _RUN_ID_SAFE.sub("-", base)[:128] or "unknown"


# ----------------------------------------------------------------------------
# Shard processing
# ----------------------------------------------------------------------------

def _download_and_decompress(s3, bucket: str, key: str, dst_dir: Path) -> Path:
    dst_dir.mkdir(parents=True, exist_ok=True)
    raw_local = dst_dir / Path(key).name
    s3.download_file(bucket, key, str(raw_local))
    if raw_local.suffix == ".gz":
        decompressed = raw_local.with_suffix("")
        with gzip.open(raw_local, "rb") as fin, open(decompressed, "wb") as fout:
            shutil.copyfileobj(fin, fout)
        raw_local.unlink(missing_ok=True)
        return decompressed
    return raw_local


def _process_shard(
    s3, sqs, queue_url: str, msg: dict, vis_interval: int, vis_extend_to: int
) -> tuple[bool, str]:
    """Process one SQS message.

    Returns ``(should_delete, reason)``. The caller deletes the message iff
    ``should_delete`` is True. ``reason`` is a short tag for logging.
    """
    body = msg["Body"]
    receipt = msg["ReceiptHandle"]

    kind, payload = _parse_s3_event(body)
    if kind == _ParseResult.TEST_EVENT:
        logger.info("s3:TestEvent (reachability ping); deleting cleanly")
        return True, "test_event"
    if kind != _ParseResult.OBJECT:
        logger.warning("unrecognized message; leaving for SQS retry → DLQ: %r", body[:200])
        return False, "unrecognized"

    bucket, key = payload  # type: ignore[misc]
    run_id = _derive_run_id(key)
    logger.info("shard start: bucket=%s key=%s run_id=%s", bucket, key, run_id)

    # Late import — extract_batch transitively imports pyiceberg/pyarrow,
    # which we don't want to load just to parse early heartbeats.
    from extract_lib import extract_batch as eb

    with VisibilityHeartbeat(
        sqs_client=sqs,
        queue_url=queue_url,
        receipt_handle=receipt,
        interval_s=vis_interval,
        extend_to_s=vis_extend_to,
    ):
        local = _download_and_decompress(s3, bucket, key, Path("/tmp/aws-llm-sqs"))
        argv = [
            "--run-id", run_id,
            "--input", str(local),
            "--resume",
            "--writer-mode", "catalog",
            "--test-name", "AsgWorker",
        ]
        rc = eb.main(argv)
    return (rc == 0), ("ok" if rc == 0 else f"extract_batch_rc={rc}")


# ----------------------------------------------------------------------------
# Self-terminate
# ----------------------------------------------------------------------------

def _self_terminate(asg_client, instance_id: str, reason: str) -> None:
    """Atomically terminate this instance and decrement ASG desired_capacity.
    The decrement is what prevents ASG from immediately replacing us."""
    logger.info("self-terminating: %s", reason)
    try:
        asg_client.terminate_instance_in_auto_scaling_group(
            InstanceId=instance_id,
            ShouldDecrementDesiredCapacity=True,
        )
    except Exception as e:  # noqa: BLE001
        logger.error("TerminateInstanceInAutoScalingGroup failed: %s", e)
    # The API call returns immediately; the EC2 shutdown happens in the
    # background. Sleep so systemd doesn't restart us before SIGTERM lands.
    time.sleep(60)
    sys.exit(0)


# ----------------------------------------------------------------------------
# Main loop
# ----------------------------------------------------------------------------

def main() -> int:
    queue_url = os.environ["JOBS_QUEUE_URL"]

    wall_cap = int(os.environ.get("WORKER_WALL_CAP_SEC", "3000"))    # 50 min
    idle_cap = int(os.environ.get("WORKER_IDLE_CAP_SEC", "300"))     # 5 min
    vis_interval = int(os.environ.get("WORKER_VIS_HEARTBEAT_S", "240"))  # 4 min
    vis_extend_to = max(vis_interval * 2, 600)                       # 10 min minimum

    # Debugging kill switch: while iterating, skip the wall-clock and idle
    # self-terminate paths so the instance stays up across SQS draining.
    # Set to "false" / unset for production (default behavior). Useful when
    # the cron-driven Ansible reconcile loop is the active dev surface and
    # we don't want the worker exiting between fixes.
    disable_self_terminate = os.environ.get(
        "DISABLE_SELF_TERMINATE", "false"
    ).lower() in ("1", "true", "yes")
    if disable_self_terminate:
        logger.warning(
            "DISABLE_SELF_TERMINATE is set; wall_cap (%ds) and idle_cap (%ds) "
            "will be ignored. Worker will run indefinitely until manually "
            "terminated. Unset for production.",
            wall_cap, idle_cap,
        )

    imds = _read_imds()
    instance_id = imds["instance_id"]
    launch_time = imds["launch_time_unix"]
    region = imds["region"]

    sqs = boto3.client("sqs", region_name=region)
    s3 = boto3.client("s3", region_name=region)
    asg = boto3.client("autoscaling", region_name=region)

    # Translate SIGTERM (sent by ASG instance-action shutdown) into a clean
    # exit so any in-flight extract_batch can call its own SIGTERM handler
    # and flush the Iceberg writer before we go.
    def _on_sigterm(signum, frame):  # noqa: ARG001
        logger.info("received SIGTERM; will exit after current shard")
        _on_sigterm.flag.set()
    _on_sigterm.flag = threading.Event()
    signal.signal(signal.SIGTERM, _on_sigterm)

    last_message_at = time.monotonic()
    logger.info(
        "worker start: instance_id=%s queue=%s wall_cap=%ds idle_cap=%ds",
        instance_id, queue_url, wall_cap, idle_cap,
    )

    while True:
        elapsed = time.time() - launch_time
        if elapsed >= wall_cap and not disable_self_terminate:
            _self_terminate(asg, instance_id, f"wall_cap reached ({elapsed:.0f}s)")

        idle = time.monotonic() - last_message_at
        if idle >= idle_cap and not disable_self_terminate:
            _self_terminate(asg, instance_id, f"idle_cap reached ({idle:.0f}s empty)")

        if _on_sigterm.flag.is_set() and not disable_self_terminate:
            _self_terminate(asg, instance_id, "SIGTERM received")

        try:
            resp = sqs.receive_message(
                QueueUrl=queue_url,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=20,
                MessageAttributeNames=["All"],
            )
        except Exception as e:  # noqa: BLE001
            logger.error("ReceiveMessage failed: %s", e)
            time.sleep(5)
            continue

        messages = resp.get("Messages") or []
        if not messages:
            continue
        msg = messages[0]
        last_message_at = time.monotonic()

        try:
            ok, reason = _process_shard(s3, sqs, queue_url, msg, vis_interval, vis_extend_to)
        except Exception as e:  # noqa: BLE001
            logger.exception("shard processing raised: %s", e)
            ok, reason = False, f"exception:{type(e).__name__}"

        if ok:
            try:
                sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=msg["ReceiptHandle"])
                logger.info("deleted message (%s)", reason)
            except Exception as e:  # noqa: BLE001
                logger.error("DeleteMessage failed: %s", e)
        else:
            logger.warning("leaving message for redelivery / DLQ (%s)", reason)


if __name__ == "__main__":
    raise SystemExit(main())
