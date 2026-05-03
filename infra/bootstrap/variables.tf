variable "region" {
  description = "AWS region for all bootstrap resources."
  type        = string
  default     = "us-east-1"
}

variable "artifacts_bucket" {
  description = "Name of the general-purpose S3 bucket for models, experiments, Athena results, and runtime TF state."
  type        = string
  default     = "aws-llm-artifacts"
}

variable "tables_bucket" {
  description = "Name of the S3 Tables (Iceberg) table bucket. NOTE: S3 Tables rejects names starting with 'aws' — keep the prefix off."
  type        = string
  default     = "nemo-tables-prod"
}

variable "tables_namespace" {
  description = "S3 Tables namespace inside the table bucket. NOTE: S3 Tables rejects namespace names starting with 'aws'."
  type        = string
  default     = "nemo"
}

variable "athena_query_scan_limit_bytes" {
  description = "Per-query bytes-scanned cutoff on the Athena workgroup."
  type        = number
  default     = 10737418240 # 10 GB
}

variable "runtime_asg_name" {
  description = "Name of the runtime Auto-Scaling Group the dispatcher Lambda scales. Hard-coded constant shared between bootstrap and runtime stacks to avoid a circular dependency."
  type        = string
  default     = "aws-llm-runtime"
}

variable "dispatch_min_files_threshold" {
  description = "Minimum SQS queue depth before a scheduled dispatcher tick scales the ASG up. Force-scale via SNS bypasses this gate."
  type        = number
  default     = 1
}

variable "state_volume_az" {
  description = "AZ for the persistent state EBS volume. EBS is AZ-locked, so this MUST match the runtime stack's availability_zone for the worker to attach."
  type        = string
  default     = "us-east-1a"
}

variable "state_volume_size_gb" {
  description = "Size of the persistent state volume in GB. Holds the model (~31G), Docker overlay layers (~25G), the venv + code, datasets, and headroom."
  type        = number
  default     = 150
}

variable "state_volume_iops" {
  description = "Provisioned IOPS for the gp3 state volume. Bumped above the 3000 default because vLLM's mmap loader produces random small reads when the 30GB FP8 checkpoint exceeds host RAM (32G on g6e.xlarge). 6000 IOPS keeps random page-fault reads at ~3ms median."
  type        = number
  default     = 6000
}

variable "state_volume_throughput_mbps" {
  description = "Provisioned throughput for the gp3 state volume in MB/s. Default gp3 (125) was the bottleneck during the 2026-05-01 model load — bumping to 500 buys ~4x sequential bandwidth so the model loads in ~1 min from EBS instead of 4 min."
  type        = number
  default     = 500
}
