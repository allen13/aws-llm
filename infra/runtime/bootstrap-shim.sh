#!/bin/bash
# bootstrap-shim.sh — bare-minimum bootstrap. Replaces the prior
# ~330-line bootstrap.sh; ALL substantive configuration moved to the
# Ansible playbook at infra/runtime/ansible/.
#
# Invoked by aws-llm-bootstrap.service (registered by user_data.tftpl)
# on every instance launch. Once successful the playbook installs a
# */5 cron that calls /opt/aws-llm-ansible/run.sh — that's the steady-
# state config-mgmt loop. See AWS.md for the lifecycle diagram.
#
# Responsibilities (deliberately small):
#   1. apt-install ansible-core + python3-boto3 / python3-docker if absent
#      (Ubuntu archive — confirmed reachable; launchpad.net is not).
#   2. Create the dedicated ansible venv at /opt/ansible-venv with the
#      Python deps Ansible's amazon.aws / community.docker modules need.
#      Avoids PEP 668 system-Python pollution.
#   3. Hand off to /opt/aws-llm-ansible/run.sh, which fetches the
#      playbook tarball from S3, runs ansible-galaxy, then ansible-
#      playbook.
#
# Errors here are fatal — without ansible installed the cron loop can
# never get installed and the system would never reconcile.

set -euxo pipefail

mkdir -p /var/log/aws-llm
chmod 0775 /var/log/aws-llm

. /etc/environment

# --- 1. Ansible inside a dedicated venv --------------------------------
# Apt's ansible on Ubuntu 22.04 (jammy) is 2.13 with old Jinja2 — silently
# skips core filter plugins (b64decode, from_json, ...) due to a Jinja2
# API rename in 3.1. Modern ansible-core in a venv avoids that, and keeps
# the system Python untouched (PEP 668).
#
# All Ansible's Python deps (boto3 for amazon.aws, docker SDK for
# community.docker) live in this venv too, separate from the app venv
# at /opt/aws-llm-extract/.venv.
if ! command -v python3 >/dev/null 2>&1; then
  DEBIAN_FRONTEND=noninteractive apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 python3-venv
fi

# Ubuntu's `python3 -m venv` needs the version-specific `python3.X-venv`
# package for `ensurepip` (the stdlib `venv` module alone isn't enough).
# Check by trying to import ensurepip; install if missing. Idempotent —
# apt-get install no-ops on satisfied requirements.
if ! python3 -c 'import ensurepip' 2>/dev/null; then
  DEBIAN_FRONTEND=noninteractive apt-get update -qq
  PYTHON_MINOR=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    "python${PYTHON_MINOR}-venv" python3-pip || \
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv python3-pip
fi

if [ ! -x /opt/ansible-venv/bin/ansible-playbook ]; then
  echo "creating /opt/ansible-venv with ansible-core + boto3 + docker"
  python3 -m venv /opt/ansible-venv
  /opt/ansible-venv/bin/pip install --quiet --upgrade pip
  /opt/ansible-venv/bin/pip install --quiet \
    "ansible-core>=2.16,<2.18" \
    boto3 botocore docker
fi

# PATH front-loads the venv's binaries so run.sh's ansible-playbook /
# ansible-galaxy invocations resolve here.
export PATH=/opt/ansible-venv/bin:$PATH

# --- 3. Hand off to run.sh ---------------------------------------------
# Bootstrap-of-bootstrap: the FIRST run pulls the tarball (via run.sh)
# which contains run.sh itself. So we need run.sh to exist before we can
# call it. Solution: do a one-shot fetch here (mirroring run.sh's first
# steps), then exec it.
mkdir -p /opt/aws-llm-ansible
aws s3 cp "s3://$ARTIFACTS_BUCKET/scripts/ansible-playbook.tgz" /tmp/ansible-playbook.tgz
tar -xzf /tmp/ansible-playbook.tgz -C /opt/aws-llm-ansible --strip-components=1
chmod +x /opt/aws-llm-ansible/run.sh

exec /opt/aws-llm-ansible/run.sh
