output "dlc_pull_policy_arn" {
  description = "ARN of the aws-llm-dlc-pull managed policy."
  value       = aws_iam_policy.dlc_pull.arn
}

output "artifacts_rw_policy_arn" {
  description = "ARN of the aws-llm-artifacts-rw managed policy."
  value       = aws_iam_policy.artifacts_rw.arn
}

output "tables_rw_policy_arn" {
  description = "ARN of the aws-llm-tables-rw managed policy."
  value       = aws_iam_policy.tables_rw.arn
}
