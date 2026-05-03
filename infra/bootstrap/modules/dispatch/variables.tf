variable "region" {
  description = "AWS region."
  type        = string
}

variable "artifacts_bucket_arn" {
  description = "ARN of the artifacts bucket. The S3 → SQS notification (declared in the storage module) routes ObjectCreated events to the jobs queue from this bucket."
  type        = string
}

variable "asg_name" {
  description = "Name of the runtime Auto-Scaling Group the dispatcher scales. Hard-coded constant shared between bootstrap and runtime stacks to avoid a circular dependency."
  type        = string
  default     = "aws-llm-runtime"
}

variable "jobs_queue_name" {
  description = "Name of the SQS queue carrying S3 ObjectCreated notifications for the inbox prefix."
  type        = string
  default     = "aws-llm-jobs"
}

variable "force_scale_topic_name" {
  description = "Name of the SNS topic operators publish to for force-scale (skip queue-depth gate)."
  type        = string
  default     = "aws-llm-force-scale"
}

variable "min_files_threshold" {
  description = "Minimum SQS queue depth (ApproximateNumberOfMessages) required before the dispatcher scales the ASG up on a scheduled tick. Force-scale via SNS bypasses this gate."
  type        = number
  default     = 1
}

variable "schedule_expression" {
  description = "EventBridge schedule expression for the periodic dispatcher tick. rate(1 minute) is the floor."
  type        = string
  default     = "rate(1 minute)"
}

variable "max_receive_count" {
  description = "Number of SQS receives before a message is moved to the DLQ. 2 = one retry then quarantine; --resume in extract_batch makes a single retry safe."
  type        = number
  default     = 2
}

variable "visibility_timeout_seconds" {
  description = "Default SQS visibility timeout. Worker extends this via ChangeMessageVisibility heartbeats while a shard is in-flight."
  type        = number
  default     = 3600
}
