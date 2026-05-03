"""Dispatcher Lambda for the aws-llm SQS → ASG batch pipeline.

Triggered by either:
  - EventBridge `rate(1 minute)` — periodic tick. Scale the ASG to 1 if there
    is queued work and no instance is running.
  - SNS `aws-llm-force-scale` — operator-initiated. Scale to 1 regardless of
    queue depth (still no-ops if an instance is already running).

The Lambda is idempotent: SetDesiredCapacity(1) on an ASG already at 1 is a
no-op at the AWS API level, but we additionally short-circuit early so logs
clearly distinguish "no work" from "scaled" from "already running" and so
we don't spam the autoscaling API. Scale-down is **not** the dispatcher's
job — instances self-terminate via TerminateInstanceInAutoScalingGroup at
the wall-clock or idle cap.

Environment:
  ASG_NAME              - name of the runtime ASG (e.g., "aws-llm-runtime")
  JOBS_QUEUE_URL        - SQS jobs queue URL
  MIN_FILES_THRESHOLD   - integer; queue depth must reach this before a
                          scheduled tick scales up. Force-scale ignores it.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_asg = boto3.client("autoscaling")
_sqs = boto3.client("sqs")


def _is_force_scale(event: dict) -> bool:
    """Return True if the event came from the SNS force-scale topic."""
    records = event.get("Records") or []
    for r in records:
        if r.get("EventSource") == "aws:sns" or r.get("eventSource") == "aws:sns":
            return True
    return False


def _queue_depth(queue_url: str) -> int:
    resp = _sqs.get_queue_attributes(
        QueueUrl=queue_url,
        AttributeNames=["ApproximateNumberOfMessages"],
    )
    return int(resp["Attributes"]["ApproximateNumberOfMessages"])


def _describe_asg(asg_name: str) -> dict[str, Any] | None:
    resp = _asg.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])
    groups = resp.get("AutoScalingGroups") or []
    return groups[0] if groups else None


def _has_in_flight_activity(asg_name: str) -> bool:
    """Detect a Successful=False or InProgress recent scaling activity. We
    avoid issuing SetDesiredCapacity while the ASG is mid-transition because
    the next describe call would still report stale state and we'd re-fire."""
    resp = _asg.describe_scaling_activities(
        AutoScalingGroupName=asg_name, MaxRecords=5
    )
    for act in resp.get("Activities") or []:
        # StatusCode is one of: PendingSpotBidPlacement, WaitingForSpotInstanceRequestId,
        # WaitingForSpotInstanceId, WaitingForInstanceId, PreInService, InProgress,
        # WaitingForELBConnectionDraining, MidLifecycleAction, WaitingForInstanceWarmup,
        # Successful, Failed, Cancelled.
        if act.get("StatusCode") in {
            "PendingSpotBidPlacement",
            "WaitingForSpotInstanceRequestId",
            "WaitingForSpotInstanceId",
            "WaitingForInstanceId",
            "PreInService",
            "InProgress",
            "WaitingForELBConnectionDraining",
            "MidLifecycleAction",
            "WaitingForInstanceWarmup",
        }:
            return True
    return False


def _set_desired(asg_name: str, desired: int) -> None:
    _asg.set_desired_capacity(
        AutoScalingGroupName=asg_name,
        DesiredCapacity=desired,
        # honor_cooldown=False: dispatch decisions are deliberate; we don't
        # want a stale cooldown timer suppressing a force-scale.
        HonorCooldown=False,
    )


def handler(event: dict, context: Any) -> dict:  # noqa: ARG001
    asg_name = os.environ["ASG_NAME"]
    queue_url = os.environ["JOBS_QUEUE_URL"]
    threshold = int(os.environ.get("MIN_FILES_THRESHOLD", "1"))

    forced = _is_force_scale(event)
    depth = _queue_depth(queue_url)
    asg = _describe_asg(asg_name)

    if asg is None:
        msg = f"ASG {asg_name!r} not found yet — runtime stack not applied? no-op"
        logger.warning(msg)
        return {"action": "noop", "reason": "asg_missing", "depth": depth, "forced": forced}

    desired = int(asg["DesiredCapacity"])
    max_size = int(asg["MaxSize"])

    if desired >= 1:
        return {"action": "noop", "reason": "already_running", "desired": desired, "depth": depth, "forced": forced}

    if max_size < 1:
        logger.warning("ASG %s has MaxSize=%d; cannot scale up", asg_name, max_size)
        return {"action": "noop", "reason": "max_size_zero", "max_size": max_size, "depth": depth, "forced": forced}

    if not forced and depth < threshold:
        return {"action": "noop", "reason": "depth_below_threshold", "depth": depth, "threshold": threshold}

    if _has_in_flight_activity(asg_name):
        return {"action": "noop", "reason": "scaling_in_progress", "depth": depth, "forced": forced}

    _set_desired(asg_name, 1)
    decision = {
        "action": "scale_up",
        "reason": "forced" if forced else "depth_at_or_above_threshold",
        "depth": depth,
        "threshold": threshold,
        "forced": forced,
        "asg_name": asg_name,
    }
    logger.info("dispatched: %s", json.dumps(decision))
    return decision
