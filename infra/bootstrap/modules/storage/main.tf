# General-purpose artifacts bucket: models, experiments, Athena results, runtime TF state.
# Prefixes (raw/, models/, experiments/, athena-results/, state/) are NOT precreated;
# S3 doesn't need them — they emerge from PutObject paths.
resource "aws_s3_bucket" "artifacts" {
  bucket        = var.artifacts_bucket
  force_destroy = true
}

resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# S3 Tables bucket. Provider 5.100.0 has known bugs on this resource
# (provider-inconsistent-result on create, encryption_configuration drift).
# Workaround: create the bucket once via `aws s3tables create-table-bucket`,
# import it into TF state, then prevent_destroy keeps Terraform from ever
# trying to recreate it. If you need to recreate, do it via CLI and re-import.
resource "aws_s3tables_table_bucket" "tables" {
  name = var.tables_bucket

  lifecycle {
    ignore_changes = [encryption_configuration, maintenance_configuration]
  }
}

resource "aws_s3tables_namespace" "aws_llm" {
  namespace        = var.tables_namespace
  table_bucket_arn = aws_s3tables_table_bucket.tables.arn
}

# S3 → SQS notification: any object landing under jobs/inbox/ ending in
# .jsonl.gz fans an event to the dispatch module's jobs queue. The queue's
# resource policy (in the dispatch module) is what authorizes S3 to send.
#
# Only one aws_s3_bucket_notification can exist per bucket, so the storage
# module owns this. If we later add other notification destinations (Lambda,
# topic), extend this single resource — don't add a second one.
resource "aws_s3_bucket_notification" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  queue {
    queue_arn     = var.jobs_queue_arn
    events        = ["s3:ObjectCreated:*"]
    filter_prefix = "jobs/inbox/"
    filter_suffix = ".jsonl.gz"
  }
}

# ----------------------------------------------------------------------------
# Persistent state volume — discovered + attached by bootstrap.sh on each
# runtime instance launch. Holds the model checkpoint, Docker layer cache,
# venv + extracted code, and datasets. Survives instance termination.
#
# Size + throughput tuned for the FP8 30B model (~31 GB on disk, vLLM's
# mmap loader produces random small reads when the checkpoint exceeds
# host RAM — 6000 IOPS / 500 MB/s keeps cold load tolerable on g6e.xlarge).
#
# AZ-locked. Must match the runtime stack's AZ.
# Single-attach. ASG max=1 makes that fine.
# prevent_destroy because losing it costs a 3.5-min S3 sync + 2-min ECR
# pull on every cold boot until repopulated.
# ----------------------------------------------------------------------------
resource "aws_ebs_volume" "state" {
  availability_zone = var.state_volume_az
  size              = var.state_volume_size_gb
  type              = "gp3"
  iops              = var.state_volume_iops
  throughput        = var.state_volume_throughput_mbps
  encrypted         = true

  tags = {
    Name    = "aws-llm-state"
    Project = "aws-llm"
    Role    = "state"
  }
}
