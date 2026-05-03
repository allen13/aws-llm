output "asg_name" {
  description = "Name of the runtime Auto-Scaling Group. Set by the dispatcher Lambda to scale 0 ↔ 1."
  value       = aws_autoscaling_group.runtime.name
}

output "asg_arn" {
  description = "ARN of the runtime Auto-Scaling Group."
  value       = aws_autoscaling_group.runtime.arn
}

output "launch_template_id" {
  description = "ID of the launch template the ASG uses."
  value       = aws_launch_template.vllm.id
}

output "iam_role_name" {
  description = "Name of the instance IAM role attached to ASG-launched instances."
  value       = aws_iam_role.instance.name
}
