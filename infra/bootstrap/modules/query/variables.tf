variable "region" {
  description = "AWS region."
  type        = string
}

variable "artifacts_bucket_name" {
  description = "Name of the artifacts bucket (used for Athena results location)."
  type        = string
}

variable "artifacts_bucket_arn" {
  description = "ARN of the artifacts bucket."
  type        = string
}

variable "tables_bucket_arn" {
  description = "ARN of the S3 Tables table bucket."
  type        = string
}

variable "tables_namespace" {
  description = "S3 Tables namespace name (also used as the Athena/Glue database name)."
  type        = string
}

variable "tables_namespace_arn" {
  description = "ARN of the S3 Tables namespace."
  type        = string
}

variable "athena_query_scan_limit_bytes" {
  description = "Per-query bytes-scanned cutoff on the Athena workgroup."
  type        = number
}
