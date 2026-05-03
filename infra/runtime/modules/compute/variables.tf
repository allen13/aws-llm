variable "region" {
  type        = string
  description = "AWS region (informational; passed through for tagging if needed)."
}

variable "instance_type" {
  type        = string
  description = "EC2 instance type."
}

variable "use_spot" {
  type        = bool
  description = "Reserved for future use. v1 of the ASG-based pipeline is on-demand only; mixed-instances policy with spot will land in a follow-up. Kept on the variable surface so callers don't need to update when it returns."
  default     = false
}

variable "spot_max_price" {
  type        = string
  description = "Reserved for future use (see use_spot)."
  default     = ""
}

variable "availability_zone" {
  type        = string
  description = "AZ for the instance."
}

variable "ami_id" {
  type        = string
  description = "AMI id (Deep Learning Base GPU Ubuntu 22.04)."
}

variable "subnet_id" {
  type        = string
  description = "Subnet to launch into."
}

variable "security_group_id" {
  type        = string
  description = "Security group for the instance."
}

variable "user_data" {
  type        = string
  description = "Rendered cloud-init user data."
}

variable "dlc_pull_policy_arn" {
  type        = string
  description = "Bootstrap-managed policy: ECR DLC pull."
}

variable "artifacts_rw_policy_arn" {
  type        = string
  description = "Bootstrap-managed policy: artifacts bucket read/write."
}

variable "tables_rw_policy_arn" {
  type        = string
  description = "Bootstrap-managed policy: S3 Tables read/write."
}

variable "root_volume_size_gb" {
  type        = number
  description = "Size of the root EBS volume in GB. Must fit AMI's 4 CUDA toolkits (~41G), the model weights (~33G), the vLLM container image (~15G), datasets, and headroom."
}

variable "jobs_queue_arn" {
  type        = string
  description = "ARN of the SQS jobs queue the worker drains. Provided by the bootstrap stack via terraform_remote_state."
}

variable "asg_name" {
  type        = string
  description = "Name of the Auto-Scaling Group to create. Constant shared with the bootstrap stack so the dispatcher Lambda can scale it."
  default     = "aws-llm-runtime"
}

variable "state_volume_id" {
  type        = string
  description = "ID of the persistent state EBS volume created by the bootstrap stack. The worker attaches this on launch via aws ec2 attach-volume."
}

variable "state_volume_arn" {
  type        = string
  description = "ARN of the persistent state EBS volume; used to scope ec2:AttachVolume IAM permission."
}
