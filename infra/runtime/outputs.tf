output "asg_name" {
  description = "Name of the runtime Auto-Scaling Group. Use this with `aws autoscaling describe-auto-scaling-groups` to discover the current InService instance."
  value       = module.compute.asg_name
}

output "asg_arn" {
  description = "ARN of the runtime Auto-Scaling Group."
  value       = module.compute.asg_arn
}

output "launch_template_id" {
  description = "ID of the launch template the ASG uses."
  value       = module.compute.launch_template_id
}

output "vpc_id" {
  description = "Runtime VPC id."
  value       = module.network.vpc_id
}

output "subnet_id" {
  description = "Public subnet id hosting the instance."
  value       = module.network.subnet_id
}

output "log_group_name" {
  description = "CloudWatch log group for runtime logs."
  value       = module.observability.log_group_name
}
