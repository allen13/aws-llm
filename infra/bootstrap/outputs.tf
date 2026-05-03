output "region" {
  description = "AWS region used by the bootstrap stack."
  value       = var.region
}

output "artifacts_bucket_id" {
  description = "ID of the general-purpose artifacts bucket."
  value       = module.storage.artifacts_bucket_id
}

output "artifacts_bucket_name" {
  description = "Name of the general-purpose artifacts bucket."
  value       = module.storage.artifacts_bucket_name
}

output "artifacts_bucket_arn" {
  description = "ARN of the general-purpose artifacts bucket."
  value       = module.storage.artifacts_bucket_arn
}

output "tables_bucket_name" {
  description = "Name of the S3 Tables table bucket."
  value       = module.storage.tables_bucket_name
}

output "tables_bucket_arn" {
  description = "ARN of the S3 Tables table bucket."
  value       = module.storage.tables_bucket_arn
}

output "tables_namespace" {
  description = "S3 Tables namespace name."
  value       = module.storage.tables_namespace
}

output "dlc_pull_policy_arn" {
  description = "ARN of the managed policy granting ECR DLC pull permissions."
  value       = module.iam.dlc_pull_policy_arn
}

output "artifacts_rw_policy_arn" {
  description = "ARN of the managed policy granting model-read / experiments-write on the artifacts bucket."
  value       = module.iam.artifacts_rw_policy_arn
}

output "tables_rw_policy_arn" {
  description = "ARN of the managed policy granting R/W on the S3 Tables namespace."
  value       = module.iam.tables_rw_policy_arn
}

output "query_lambda_arn" {
  description = "ARN of the aws-llm-table-query Lambda."
  value       = module.query.query_lambda_arn
}

output "athena_workgroup_name" {
  description = "Name of the Athena workgroup."
  value       = module.query.athena_workgroup_name
}

output "jobs_queue_url" {
  description = "URL of the SQS jobs queue. Consumed by the runtime worker."
  value       = module.dispatch.jobs_queue_url
}

output "jobs_queue_arn" {
  description = "ARN of the SQS jobs queue. Consumed by the runtime IAM."
  value       = module.dispatch.jobs_queue_arn
}

output "jobs_queue_name" {
  description = "Name of the SQS jobs queue."
  value       = module.dispatch.jobs_queue_name
}

output "jobs_dlq_url" {
  description = "URL of the SQS jobs dead-letter queue."
  value       = module.dispatch.jobs_dlq_url
}

output "force_scale_topic_arn" {
  description = "ARN of the SNS force-scale topic. Operators publish to this to trigger a scale-up regardless of queue depth."
  value       = module.dispatch.force_scale_topic_arn
}

output "dispatcher_lambda_name" {
  description = "Name of the dispatcher Lambda."
  value       = module.dispatch.dispatcher_lambda_name
}

output "runtime_asg_name" {
  description = "Name of the runtime ASG the dispatcher scales (constant, must match runtime stack)."
  value       = var.runtime_asg_name
}

output "state_volume_id" {
  description = "ID of the persistent state EBS volume the worker discovers + attaches by tag."
  value       = module.storage.state_volume_id
}

output "state_volume_arn" {
  description = "ARN of the persistent state EBS volume."
  value       = module.storage.state_volume_arn
}

output "state_volume_az" {
  description = "AZ where the state volume lives — runtime stack must launch in this AZ for the worker to attach."
  value       = module.storage.state_volume_az
}
