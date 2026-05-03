#!/bin/bash
# /opt/aws-llm-ansible/run.sh — the one-true entrypoint for running the
# Ansible playbook on this instance.
#
# Invoked by:
#   1. systemd unit aws-llm-bootstrap.service on first launch (via
#      bootstrap-shim.sh which apt-installs ansible first, then exec's
#      this script).
#   2. cron */5 (installed by the ansible_cron role on first successful
#      run of the playbook). flock-guarded so overlapping runs don't race.
#
# Responsibilities (deliberately small):
#   - Pull s3://$ARTIFACTS_BUCKET/scripts/ansible-playbook.tgz to /tmp
#     (only re-extract on etag change)
#   - ansible-galaxy collection install (only on requirements.yml change)
#   - ansible-playbook with /etc/environment values as --extra-vars
#
# Errors are logged to /var/log/aws-llm/ansible-cron.log via the cron's
# stdout redirect; on the systemd path they go to the unit's journal.

set -euo pipefail
set -x

. /etc/environment

# Ensure the venv's ansible-* binaries are on PATH. The shim adds these
# already, but cron invokes run.sh with a minimal env.
export PATH=/opt/ansible-venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

PLAYBOOK_DIR=/opt/aws-llm-ansible
TARBALL=/tmp/ansible-playbook.tgz
S3_KEY="scripts/ansible-playbook.tgz"
ETAG_MARKER="$PLAYBOOK_DIR/.tarball-etag"
GALAXY_MARKER="$PLAYBOOK_DIR/.galaxy-etag"
LOCK_FILE=/var/run/aws-llm-ansible.lock

# flock guard: re-entrant via inheritable fd. If another invocation is
# holding the lock, exit silently (cron will catch the next tick).
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "ansible run skipped: another invocation holds $LOCK_FILE"
  exit 0
fi

mkdir -p "$PLAYBOOK_DIR"

# 1. Fetch tarball etag from S3, compare against marker. Skip extract on match.
TARBALL_ETAG=$(aws s3api head-object --bucket "$ARTIFACTS_BUCKET" --key "$S3_KEY" --query ETag --output text 2>/dev/null | tr -d '"' || echo "")
DEPLOYED_ETAG=$(cat "$ETAG_MARKER" 2>/dev/null || echo "")

if [ -z "$TARBALL_ETAG" ]; then
  echo "ERROR: cannot read s3 etag for $S3_KEY; aborting" >&2
  exit 1
fi

if [ "$TARBALL_ETAG" != "$DEPLOYED_ETAG" ] || [ ! -f "$PLAYBOOK_DIR/playbook.yml" ]; then
  echo "fetching playbook (etag $DEPLOYED_ETAG -> $TARBALL_ETAG)"
  aws s3 cp "s3://$ARTIFACTS_BUCKET/$S3_KEY" "$TARBALL"
  # Atomic-ish replace: extract into a sibling tmpdir and rsync over. Avoids
  # half-extracted state if the cron is interrupted mid-tar.
  STAGE=$(mktemp -d)
  tar -xzf "$TARBALL" -C "$STAGE" --strip-components=1
  rsync -a --delete "$STAGE/" "$PLAYBOOK_DIR/"
  rm -rf "$STAGE"
  echo "$TARBALL_ETAG" > "$ETAG_MARKER"
else
  echo "playbook unchanged (etag $TARBALL_ETAG)"
fi

# 2. Galaxy collections. Re-install only when requirements.yml changed.
# Install path is pinned to the playbook dir's `collections/` so the
# ansible.cfg `collections_path` lookup is deterministic — independent of
# which user, HOME, or ANSIBLE_HOME ansible-playbook inherits.
REQ_HASH=$(sha256sum "$PLAYBOOK_DIR/requirements.yml" | awk '{print $1}')
INSTALLED_HASH=$(cat "$GALAXY_MARKER" 2>/dev/null || echo "")
COLLECTIONS_DIR="$PLAYBOOK_DIR/collections"
if [ "$REQ_HASH" != "$INSTALLED_HASH" ] || [ ! -d "$COLLECTIONS_DIR/ansible_collections/amazon/aws" ]; then
  echo "installing/updating ansible collections into $COLLECTIONS_DIR (req sha $REQ_HASH)"
  ansible-galaxy collection install -r "$PLAYBOOK_DIR/requirements.yml" \
    -p "$COLLECTIONS_DIR" >&2
  echo "$REQ_HASH" > "$GALAXY_MARKER"
fi

# 3. Run the playbook. Variables come from /etc/environment via --extra-vars.
cd "$PLAYBOOK_DIR"
exec ansible-playbook -i inventory/local.yml playbook.yml \
  --extra-vars "artifacts_bucket=$ARTIFACTS_BUCKET" \
  --extra-vars "aws_region=$AWS_REGION" \
  --extra-vars "model_s3_uri=$MODEL_S3_URI" \
  --extra-vars "vllm_image_uri=$VLLM_IMAGE_URI" \
  --extra-vars "max_model_len=$MAX_MODEL_LEN" \
  --extra-vars "max_num_seqs=$MAX_NUM_SEQS" \
  --extra-vars "model_name=$MODEL_NAME" \
  --extra-vars "enable_thinking=${ENABLE_THINKING:-false}" \
  --extra-vars "jobs_queue_url=$JOBS_QUEUE_URL" \
  --extra-vars "asg_name=$ASG_NAME" \
  --extra-vars "state_volume_id=$STATE_VOLUME_ID"
