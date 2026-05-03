# Makefile — aws-llm inner-loop targets.
#
# Reads INSTANCE_ID from `terraform -chdir=infra/runtime output` if not
# overridden in the env. RUN_ID and EXPERIMENT are required for `make run`.
# `make exec CMD='...'` is the no-plugin debug primitive — see DEPLOY.md §4.

ARTIFACTS  := s3://aws-llm-artifacts
INBOX      := $(ARTIFACTS)/jobs/inbox
ASG_NAME   ?= aws-llm-runtime
JOBS_QUEUE_URL ?= $(shell terraform -chdir=infra/bootstrap output -raw jobs_queue_url 2>/dev/null)
JOBS_DLQ_URL   ?= $(shell terraform -chdir=infra/bootstrap output -raw jobs_dlq_url 2>/dev/null)
FORCE_SCALE_TOPIC_ARN ?= $(shell terraform -chdir=infra/bootstrap output -raw force_scale_topic_arn 2>/dev/null)
# Resolve the current InService instance from the ASG (instances are now
# ephemeral; there is no stable instance_id output).
INSTANCE_ID ?= $(shell aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names $(ASG_NAME) --query 'AutoScalingGroups[0].Instances[?LifecycleState==`InService`].InstanceId' --output text 2>/dev/null)
SHA        := $(shell git rev-parse HEAD 2>/dev/null)$(shell [ -n "$$(git status --porcelain)" ] && echo "-dirty")

.PHONY: help
help:
	@echo "aws-llm Makefile targets:"
	@echo ""
	@echo "  -- Job submission (S3 → SQS → ASG pipeline) --"
	@echo "  submit FILES='a.jsonl.gz b.jsonl.gz'   stage shards to s3://aws-llm-artifacts/jobs/inbox/"
	@echo "  submit-force                           SNS publish to force-scale even with empty queue"
	@echo "  queue-depth                            ApproximateNumberOfMessages on the jobs queue + DLQ"
	@echo "  worker-status                          ASG state + current InService instance id"
	@echo ""
	@echo "  -- Code + config --"
	@echo "  sync-code-s3-only    package extract/ and push to S3"
	@echo "  sync-code            sync-code-s3-only + reinstall on the live instance (if any)"
	@echo "  sync-ansible         package infra/runtime/ansible/ and push to S3 (cron picks up in <5 min)"
	@echo ""
	@echo "  -- Observation --"
	@echo "  logs                 tail CloudWatch /aws/aws-llm/extract"
	@echo "  cloud-init-logs      tail CloudWatch /aws/aws-llm/cloud-init"
	@echo "  ansible-logs         tail /var/log/aws-llm/ansible*.log on the live instance"
	@echo "  exec CMD='…'         one-shot SSM command against the current InService instance"
	@echo "  shell                interactive SSM session (needs session-manager-plugin)"
	@echo ""
	@echo "  -- Manual ASG controls (rare) --"
	@echo "  terminate-worker     terminate the InService instance + decrement desired_capacity"
	@echo "  scale-down           hard-set ASG desired_capacity to 0"
	@echo ""
	@echo "  -- Stack lifecycle --"
	@echo "  bootstrap-apply      terraform apply infra/bootstrap/"
	@echo "  runtime-apply        terraform apply infra/runtime/"
	@echo "  runtime-destroy      terraform destroy infra/runtime/ (keeps bootstrap)"

# ----------------------------------------------------------------------------
# Code sync
# ----------------------------------------------------------------------------

.PHONY: sync-code-s3-only
sync-code-s3-only:
	tar --exclude=__pycache__ --exclude='*.egg-info' --exclude='.pytest_cache' \
	    -czf /tmp/extract.tgz extract/
	aws s3 cp /tmp/extract.tgz $(ARTIFACTS)/code/$(SHA)/extract.tgz --quiet
	aws s3 cp /tmp/extract.tgz $(ARTIFACTS)/code/latest/extract.tgz --quiet
	@echo "staged $(SHA) → $(ARTIFACTS)/code/latest/extract.tgz"

.PHONY: sync-ansible
sync-ansible:
	tar --exclude=__pycache__ --exclude='*.retry' \
	    -czf /tmp/ansible-playbook.tgz -C infra/runtime/ansible .
	aws s3 cp /tmp/ansible-playbook.tgz $(ARTIFACTS)/scripts/$(SHA)/ansible-playbook.tgz --quiet
	aws s3 cp /tmp/ansible-playbook.tgz $(ARTIFACTS)/scripts/ansible-playbook.tgz --quiet
	@echo "staged $(SHA) → $(ARTIFACTS)/scripts/ansible-playbook.tgz"
	@echo "(cron picks up within 5 min; SSM-restart aws-llm-bootstrap for immediate effect)"

.PHONY: sync-code
sync-code: sync-code-s3-only
	@test -n "$(INSTANCE_ID)" || (echo "no InService instance under ASG $(ASG_NAME); skipping in-place reinstall" && exit 0)
	@CID=$$(aws ssm send-command --instance-ids "$(INSTANCE_ID)" \
	   --document-name AWS-RunShellScript \
	   --comment "sync-code $(SHA)" \
	   --parameters 'commands=["set -euo pipefail","aws s3 cp $(ARTIFACTS)/code/latest/extract.tgz /tmp/extract.tgz","mkdir -p /opt/aws-llm-extract","tar -xzf /tmp/extract.tgz -C /opt/aws-llm-extract --strip-components=1","/opt/aws-llm-extract/.venv/bin/pip install --quiet -e /opt/aws-llm-extract"]' \
	   --query Command.CommandId --output text); \
	 echo "ssm command-id: $$CID"; \
	 aws ssm wait command-executed --command-id $$CID --instance-id $(INSTANCE_ID); \
	 echo "sync-code: ok"

# ----------------------------------------------------------------------------
# Job submission (S3 → SQS → ASG)
# ----------------------------------------------------------------------------

.PHONY: submit
submit:
	@test -n "$(FILES)" || (echo "usage: make submit FILES='shard-00.jsonl.gz [shard-01.jsonl.gz ...]'" && exit 1)
	@for f in $(FILES); do \
	  test -f "$$f" || { echo "missing local file: $$f"; exit 1; }; \
	  echo "  → s3://$(notdir $(INBOX))/$$(basename $$f)"; \
	  aws s3 cp "$$f" "$(INBOX)/$$(basename $$f)" --quiet; \
	done
	@echo "submitted $(words $(FILES)) file(s); dispatcher will scale within ~1 min"

.PHONY: submit-force
submit-force:
	@test -n "$(FORCE_SCALE_TOPIC_ARN)" || (echo "no FORCE_SCALE_TOPIC_ARN — run bootstrap-apply first" && exit 1)
	@aws sns publish --topic-arn "$(FORCE_SCALE_TOPIC_ARN)" --message '{"force": true}' --message-attributes '{"source":{"DataType":"String","StringValue":"makefile"}}' --output text >/dev/null
	@echo "force-scale published to $(FORCE_SCALE_TOPIC_ARN)"

.PHONY: queue-depth
queue-depth:
	@test -n "$(JOBS_QUEUE_URL)" || (echo "no JOBS_QUEUE_URL — run bootstrap-apply first" && exit 1)
	@printf "jobs:  "
	@aws sqs get-queue-attributes --queue-url "$(JOBS_QUEUE_URL)" --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible --query 'Attributes' --output json
	@if [ -n "$(JOBS_DLQ_URL)" ]; then \
	  printf "dlq:   "; \
	  aws sqs get-queue-attributes --queue-url "$(JOBS_DLQ_URL)" --attribute-names ApproximateNumberOfMessages --query 'Attributes' --output json; \
	fi

.PHONY: worker-status
worker-status:
	@aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names $(ASG_NAME) \
	   --query 'AutoScalingGroups[0].{name: AutoScalingGroupName, min: MinSize, max: MaxSize, desired: DesiredCapacity, instances: Instances[].{id: InstanceId, state: LifecycleState, health: HealthStatus}}' \
	   --output json
	@if [ -n "$(INSTANCE_ID)" ]; then \
	  echo ""; echo "current InService instance: $(INSTANCE_ID)"; \
	  echo "tail logs:  make logs"; \
	  echo "ssm:        make exec CMD='journalctl -u aws-llm-sqs-worker.service --no-pager -n 50'"; \
	fi

# ----------------------------------------------------------------------------
# Observation
# ----------------------------------------------------------------------------

.PHONY: logs
logs:
	aws logs tail /aws/aws-llm/extract --follow

.PHONY: cloud-init-logs
cloud-init-logs:
	aws logs tail /aws/aws-llm/cloud-init --follow

.PHONY: ansible-logs
ansible-logs:
	@test -n "$(INSTANCE_ID)" || (echo "no InService instance under ASG $(ASG_NAME)" && exit 1)
	@$(MAKE) --no-print-directory exec CMD='tail -n 80 /var/log/aws-llm/ansible-cron.log /var/log/aws-llm/ansible.log 2>/dev/null'

.PHONY: status
status:
	@echo "ASG_NAME:       $(ASG_NAME)"
	@echo "INSTANCE_ID:    $(INSTANCE_ID)"
	@echo "JOBS_QUEUE_URL: $(JOBS_QUEUE_URL)"
	@if [ -n "$(INSTANCE_ID)" ]; then \
	  $(MAKE) --no-print-directory exec CMD='curl -s http://localhost:8000/v1/models | jq .data[].id 2>/dev/null || echo "vLLM not responding"'; \
	else \
	  echo "(no InService instance — submit work to bring one up)"; \
	fi

# ----------------------------------------------------------------------------
# Debug
# ----------------------------------------------------------------------------

.PHONY: exec
exec:
	@test -n "$(INSTANCE_ID)" || (echo "no InService instance under ASG $(ASG_NAME) — submit work to bring one up" && exit 1)
	@test -n "$(CMD)" || (echo 'usage: make exec CMD="docker logs --tail 50 vllm"' && exit 1)
	@CID=$$(aws ssm send-command --instance-ids "$(INSTANCE_ID)" \
	   --document-name AWS-RunShellScript \
	   --parameters "commands=[\"$(CMD)\"]" \
	   --query Command.CommandId --output text); \
	 aws ssm wait command-executed --command-id $$CID --instance-id $(INSTANCE_ID); \
	 aws ssm get-command-invocation --command-id $$CID --instance-id $(INSTANCE_ID) \
	   --query StandardOutputContent --output text

.PHONY: shell
shell:
	@# Requires session-manager-plugin. Use `make exec CMD='...'` if you don't have it.
	@test -n "$(INSTANCE_ID)" || (echo "no InService instance under ASG $(ASG_NAME)" && exit 1)
	aws ssm start-session --target $(INSTANCE_ID)

# ----------------------------------------------------------------------------
# Lifecycle
# ----------------------------------------------------------------------------

# Manually drop the current InService instance (e.g., to bail out of a wedged
# extract). The ASG sees DesiredCapacity decremented by the API call so it
# won't immediately respawn; the dispatcher will scale back up at the next
# tick if there's still queued work.
.PHONY: terminate-worker
terminate-worker:
	@test -n "$(INSTANCE_ID)" || (echo "no InService instance under ASG $(ASG_NAME)" && exit 1)
	aws autoscaling terminate-instance-in-auto-scaling-group \
	  --instance-id "$(INSTANCE_ID)" \
	  --should-decrement-desired-capacity
	@echo "terminate scheduled for $(INSTANCE_ID)"

# Force the ASG back to desired=0 (cancel any in-flight scale-up). Use this
# if you need to hard-stop the pipeline without draining the queue.
.PHONY: scale-down
scale-down:
	aws autoscaling set-desired-capacity \
	  --auto-scaling-group-name $(ASG_NAME) \
	  --desired-capacity 0 \
	  --honor-cooldown
	@echo "ASG $(ASG_NAME) desired=0"

.PHONY: bootstrap-apply
bootstrap-apply:
	cd infra/bootstrap && terraform apply

.PHONY: runtime-apply
runtime-apply:
	cd infra/runtime && terraform apply

.PHONY: runtime-destroy
runtime-destroy:
	cd infra/runtime && terraform destroy
