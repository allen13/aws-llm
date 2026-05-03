"""Spot interruption watcher — polls IMDSv2 every 5 s.

Runs as ``spot-watcher.service`` (systemd) on the EC2 instance, or
ad-hoc via ``python -m extract_lib.spot_watcher``.

On a 200 from ``/latest/meta-data/spot/instance-action`` it:

1. SIGTERMs the extract process (pidfile from ``EXTRACT_PIDFILE``).
2. Waits 90 s; SIGKILLs if still alive.
3. Updates ``experiment_runs[run_id].status = 'interrupted'``.
4. Logs to stderr and exits 0.

See PLAN.md §Spot handling.
"""

from __future__ import annotations

import os
import signal
import sys
import time

POLL_INTERVAL_S = 5
GRACE_TERM_SECONDS = 90
IMDS_BASE = "http://169.254.169.254"
IMDS_TOKEN_TTL = "21600"


def _log(msg: str) -> None:
    print(f"[spot-watcher] {msg}", file=sys.stderr, flush=True)


def _get_token() -> str | None:
    try:
        import httpx  # type: ignore
    except Exception:  # pragma: no cover
        return None
    try:
        r = httpx.put(
            f"{IMDS_BASE}/latest/api/token",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": IMDS_TOKEN_TTL},
            timeout=2.0,
        )
        if r.status_code == 200:
            return r.text.strip()
    except Exception as e:
        _log(f"token fetch failed: {e}")
    return None


def _check_interruption(token: str) -> bool:
    """Return True iff IMDS is signalling a spot interruption."""
    try:
        import httpx  # type: ignore
    except Exception:  # pragma: no cover
        return False
    try:
        r = httpx.get(
            f"{IMDS_BASE}/latest/meta-data/spot/instance-action",
            headers={"X-aws-ec2-metadata-token": token},
            timeout=2.0,
        )
        return r.status_code == 200
    except Exception:
        return False


def _read_pidfile(path: str) -> int | None:
    try:
        with open(path) as f:
            return int(f.read().strip())
    except Exception as e:
        _log(f"pidfile read failed ({path}): {e}")
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _terminate(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
        _log(f"sent SIGTERM to pid {pid}")
    except OSError as e:
        _log(f"SIGTERM failed: {e}")


def _kill(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
        _log(f"sent SIGKILL to pid {pid}")
    except OSError as e:
        _log(f"SIGKILL failed: {e}")


def _mark_interrupted(run_id: str) -> None:
    """Best-effort update to ``experiment_runs[run_id].status='interrupted'``."""
    try:
        from .config import ExtractConfig
        from .iceberg_writer import IcebergWriter

        cfg = ExtractConfig()
        writer = IcebergWriter(cfg, run_id=run_id, mode="catalog")
        writer.finish_experiment_run(run_id, status="interrupted")
        _log(f"marked experiment_runs[{run_id}] status=interrupted")
    except Exception as e:
        _log(f"failed to mark interrupted: {e}")


def main() -> int:
    pidfile = os.environ.get("EXTRACT_PIDFILE", "/tmp/aws-llm-extract.pid")
    run_id = os.environ.get("RUN_ID", "")
    _log(f"started; pidfile={pidfile} run_id={run_id or '<unset>'}")

    while True:
        token = _get_token()
        if token is None:
            time.sleep(POLL_INTERVAL_S)
            continue
        if _check_interruption(token):
            _log("interruption notice received")
            pid = _read_pidfile(pidfile)
            if pid is None:
                _log("no pid; exiting")
                return 0
            _terminate(pid)
            deadline = time.monotonic() + GRACE_TERM_SECONDS
            while time.monotonic() < deadline:
                if not _pid_alive(pid):
                    break
                time.sleep(1)
            if _pid_alive(pid):
                _kill(pid)
            if run_id:
                _mark_interrupted(run_id)
            return 0
        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
