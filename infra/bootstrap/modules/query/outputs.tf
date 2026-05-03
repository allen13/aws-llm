output "query_lambda_arn" {
  description = "ARN of the aws-llm-table-query Lambda."
  value       = aws_lambda_function.aws_llm_table_query.arn
}

output "query_lambda_name" {
  description = "Name of the aws-llm-table-query Lambda."
  value       = aws_lambda_function.aws_llm_table_query.function_name
}

output "athena_workgroup_name" {
  description = "Name of the Athena workgroup."
  value       = aws_athena_workgroup.aws_llm.name
}
