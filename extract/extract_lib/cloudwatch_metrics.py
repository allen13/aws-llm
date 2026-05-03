"""CloudWatch metrics publisher — closes the observability gap.

PLAN.md §Operational gaps: push ``req_per_sec``, ``error_rate``,
``prefix_cache_hit_ratio``, ``gpu_util`` to CloudWatch every 60 s.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import ExtractConfig


METRIC_NAMESPACE = "aws-llm"
PERIOD_SECONDS = 60


class MetricsPublisher:
    """Wraps boto3 CloudWatch ``put_metric_data`` calls.

    Construction is cheap and does not contact the network; AWS auth is
    resolved on the first ``publish()``.
    """

    def __init__(self, cfg: "ExtractConfig", run_id: str) -> None:
        self.cfg = cfg
        self.run_id = run_id
        self._client = None  # lazy

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            import boto3  # type: ignore
        except Exception as e:  # pragma: no cover
            raise RuntimeError(
                "boto3 is required for CloudWatch metrics publishing"
            ) from e
        self._client = boto3.client("cloudwatch", region_name=self.cfg.aws_region)
        return self._client

    def publish(
        self,
        req_per_sec: float,
        error_rate: float,
        prefix_cache_hit_ratio: float,
        gpu_util: float,
    ) -> None:
        client = self._get_client()
        ts = datetime.now(timezone.utc)
        dims = [{"Name": "RunId", "Value": self.run_id}]
        client.put_metric_data(
            Namespace=METRIC_NAMESPACE,
            MetricData=[
                {
                    "MetricName": "ReqPerSec",
                    "Value": float(req_per_sec),
                    "Unit": "Count/Second",
                    "Timestamp": ts,
                    "Dimensions": dims,
                    "StorageResolution": PERIOD_SECONDS,
                },
                {
                    "MetricName": "ErrorRate",
                    "Value": float(error_rate),
                    "Unit": "Percent",
                    "Timestamp": ts,
                    "Dimensions": dims,
                    "StorageResolution": PERIOD_SECONDS,
                },
                {
                    "MetricName": "PrefixCacheHitRatio",
                    "Value": float(prefix_cache_hit_ratio),
                    "Unit": "None",
                    "Timestamp": ts,
                    "Dimensions": dims,
                    "StorageResolution": PERIOD_SECONDS,
                },
                {
                    "MetricName": "GpuUtil",
                    "Value": float(gpu_util),
                    "Unit": "Percent",
                    "Timestamp": ts,
                    "Dimensions": dims,
                    "StorageResolution": PERIOD_SECONDS,
                },
            ],
        )


# TODO: confirm exact metric name. vLLM exposes ``vllm:gpu_cache_usage_perc``
# in 0.20.x; older builds used ``vllm:gpu_cache_usage``. Adjust the regex
# below once the live ``/metrics`` payload from the DLC is captured.
_GPU_UTIL_PATTERNS = (
    re.compile(r"^vllm:gpu_cache_usage_perc(?:\{[^}]*\})?\s+([0-9eE.+-]+)\s*$", re.M),
    re.compile(r"^vllm:gpu_cache_usage(?:\{[^}]*\})?\s+([0-9eE.+-]+)\s*$", re.M),
)


def gpu_util_from_metrics(vllm_metrics_text: str) -> float:
    """Parse the vLLM Prometheus ``/metrics`` scrape and return GPU util.

    Returns the value as a float in the range ``[0.0, 1.0]`` (Prometheus
    exposes it as a fraction; convert to percent at the call site if
    desired). Returns 0.0 if no matching metric is found.
    """
    for pattern in _GPU_UTIL_PATTERNS:
        m = pattern.search(vllm_metrics_text)
        if m:
            try:
                return float(m.group(1))
            except ValueError:  # pragma: no cover
                continue
    return 0.0
