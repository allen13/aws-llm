output "artifacts_bucket_id" {
  description = "ID of the artifacts bucket."
  value       = aws_s3_bucket.artifacts.id
}

output "artifacts_bucket_name" {
  description = "Name of the artifacts bucket."
  value       = aws_s3_bucket.artifacts.bucket
}

output "artifacts_bucket_arn" {
  description = "ARN of the artifacts bucket."
  value       = aws_s3_bucket.artifacts.arn
}

output "tables_bucket_name" {
  description = "Name of the S3 Tables table bucket."
  value       = aws_s3tables_table_bucket.tables.name
}

output "tables_bucket_arn" {
  description = "ARN of the S3 Tables table bucket."
  value       = aws_s3tables_table_bucket.tables.arn
}

output "tables_namespace" {
  description = "S3 Tables namespace name."
  value       = aws_s3tables_namespace.aws_llm.namespace
}

output "state_volume_id" {
  description = "ID of the persistent state EBS volume."
  value       = aws_ebs_volume.state.id
}

output "state_volume_arn" {
  description = "ARN of the persistent state EBS volume."
  value       = aws_ebs_volume.state.arn
}

output "state_volume_az" {
  description = "AZ where the state volume lives. Must match the runtime ASG's AZ."
  value       = aws_ebs_volume.state.availability_zone
}
