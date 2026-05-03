variable "artifacts_bucket_name" {
  description = "Name of the artifacts bucket."
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
  description = "S3 Tables namespace name."
  type        = string
}

variable "tables_namespace_arn" {
  description = "ARN of the S3 Tables namespace inside the table bucket."
  type        = string
}
