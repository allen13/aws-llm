variable "artifacts_bucket" {
  description = "Name of the general-purpose artifacts S3 bucket."
  type        = string
}

variable "tables_bucket" {
  description = "Name of the S3 Tables table bucket."
  type        = string
}

variable "tables_namespace" {
  description = "S3 Tables namespace inside the table bucket."
  type        = string
}

variable "jobs_queue_arn" {
  description = "ARN of the SQS jobs queue that S3 ObjectCreated events on jobs/inbox/*.jsonl.gz are routed to. Provided by the dispatch module."
  type        = string
}

variable "state_volume_az" {
  description = "AZ for the persistent state EBS volume. Must match the runtime stack's AZ — EBS is AZ-locked."
  type        = string
}

variable "state_volume_size_gb" {
  description = "Size of the persistent state volume in GB."
  type        = number
}

variable "state_volume_iops" {
  description = "Provisioned IOPS for the gp3 state volume."
  type        = number
}

variable "state_volume_throughput_mbps" {
  description = "Provisioned throughput for the gp3 state volume (MB/s)."
  type        = number
}
