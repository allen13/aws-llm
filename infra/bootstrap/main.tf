provider "aws" {
  region = var.region
}

# Construct the artifacts bucket ARN locally so the dispatch module doesn't
# need a reference into the storage module. Without this, storage would
# depend on dispatch (jobs_queue_arn for bucket notification) and dispatch
# would depend on storage (artifacts_bucket_arn for the SQS queue policy) —
# a cycle. The bucket name is a static input, so its ARN is known a priori.
locals {
  artifacts_bucket_arn = "arn:aws:s3:::${var.artifacts_bucket}"
}

module "storage" {
  source = "./modules/storage"

  artifacts_bucket = var.artifacts_bucket
  tables_bucket    = var.tables_bucket
  tables_namespace = var.tables_namespace

  # Wire the S3 ObjectCreated notification (jobs/inbox/*.jsonl.gz) into the
  # dispatch module's SQS queue. Notification is declared here so storage
  # owns the only aws_s3_bucket_notification on this bucket (AWS allows one).
  jobs_queue_arn = module.dispatch.jobs_queue_arn

  # Persistent state EBS volume.
  state_volume_az              = var.state_volume_az
  state_volume_size_gb         = var.state_volume_size_gb
  state_volume_iops            = var.state_volume_iops
  state_volume_throughput_mbps = var.state_volume_throughput_mbps
}

module "iam" {
  source = "./modules/iam"

  artifacts_bucket_name = module.storage.artifacts_bucket_name
  artifacts_bucket_arn  = module.storage.artifacts_bucket_arn
  tables_bucket_arn     = module.storage.tables_bucket_arn
  tables_namespace      = module.storage.tables_namespace
  tables_namespace_arn  = "${module.storage.tables_bucket_arn}/namespace/${module.storage.tables_namespace}"
}

module "query" {
  source = "./modules/query"

  region                        = var.region
  artifacts_bucket_name         = module.storage.artifacts_bucket_name
  artifacts_bucket_arn          = module.storage.artifacts_bucket_arn
  tables_bucket_arn             = module.storage.tables_bucket_arn
  tables_namespace              = module.storage.tables_namespace
  tables_namespace_arn          = "${module.storage.tables_bucket_arn}/namespace/${module.storage.tables_namespace}"
  athena_query_scan_limit_bytes = var.athena_query_scan_limit_bytes
}

module "dispatch" {
  source = "./modules/dispatch"

  region               = var.region
  artifacts_bucket_arn = local.artifacts_bucket_arn # static, breaks the storage↔dispatch cycle
  asg_name             = var.runtime_asg_name
  min_files_threshold  = var.dispatch_min_files_threshold
}
