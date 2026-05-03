output "jobs_queue_url" {
  description = "URL of the SQS jobs queue (consumed by the worker on the runtime instance)."
  value       = aws_sqs_queue.jobs.id
}

output "jobs_queue_arn" {
  description = "ARN of the SQS jobs queue. Required by the storage module to wire S3 → SQS notifications, and by the runtime IAM to grant ReceiveMessage / DeleteMessage."
  value       = aws_sqs_queue.jobs.arn
}

output "jobs_queue_name" {
  description = "Name of the SQS jobs queue."
  value       = aws_sqs_queue.jobs.name
}

output "jobs_dlq_url" {
  description = "URL of the SQS jobs dead-letter queue."
  value       = aws_sqs_queue.jobs_dlq.id
}

output "jobs_dlq_arn" {
  description = "ARN of the SQS jobs dead-letter queue."
  value       = aws_sqs_queue.jobs_dlq.arn
}

output "force_scale_topic_arn" {
  description = "ARN of the SNS topic operators publish to for a force-scale event."
  value       = aws_sns_topic.force_scale.arn
}

output "force_scale_topic_name" {
  description = "Name of the SNS force-scale topic."
  value       = aws_sns_topic.force_scale.name
}

output "dispatcher_lambda_arn" {
  description = "ARN of the dispatcher Lambda."
  value       = aws_lambda_function.dispatcher.arn
}

output "dispatcher_lambda_name" {
  description = "Name of the dispatcher Lambda."
  value       = aws_lambda_function.dispatcher.function_name
}
