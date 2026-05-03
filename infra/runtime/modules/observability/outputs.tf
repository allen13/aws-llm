output "log_group_name" {
  description = "CloudWatch log group for runtime logs."
  value       = aws_cloudwatch_log_group.runtime.name
}

output "alarms_topic_arn" {
  description = "SNS topic ARN for runtime alarms."
  value       = aws_sns_topic.alarms.arn
}
