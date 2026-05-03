variable "region" {
  type        = string
  description = "AWS region for runtime resources."
  default     = "us-east-1"
}

variable "instance_type" {
  type        = string
  description = "EC2 instance type for the vLLM host. g6e.xlarge is sized to fit the 4-vCPU G/VT quota; bootstrap-shim.sh mounts the instance-store NVMe at /mnt/nvme and stages the model there, sidestepping the page-cache thrash that 32 GB host RAM otherwise causes (see MEMORY_NOTES.md)."
  default     = "g6e.xlarge"
}

variable "use_spot" {
  type        = bool
  description = "If true, request a spot instance instead of on-demand."
  default     = false
}

variable "spot_max_price" {
  type        = string
  description = "Optional spot bid cap; empty string = pay market price."
  default     = ""
}

variable "availability_zone" {
  type        = string
  description = "AZ for the public subnet and EC2 instance."
  default     = "us-east-1a"
}

variable "hf_model_id" {
  type        = string
  description = "Hugging Face model id (informational, used by staging script)."
  default     = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-FP8"
}

variable "model_s3_uri" {
  type        = string
  description = "S3 URI of staged model weights to sync into the instance."
  default     = "s3://aws-llm-artifacts/models/nemotron-nano-30b-fp8/"
}

variable "vllm_image_uri" {
  type        = string
  description = "ECR URI of the AWS DLC vLLM image."
  default     = "763104351884.dkr.ecr.us-east-1.amazonaws.com/vllm:0.20.0-gpu-py312-cu130-ubuntu22.04-ec2"
}

variable "max_model_len" {
  type        = number
  description = "vLLM --max-model-len."
  default     = 8192
}

variable "max_num_seqs" {
  type        = number
  description = "vLLM --max-num-seqs."
  default     = 32
}

variable "enable_thinking" {
  type        = bool
  description = "Enable Nemotron reasoning/thinking mode (paper experiment only)."
  default     = false
}

variable "uptime_alarm_threshold_hours" {
  type        = number
  description = "Hours of instance uptime before the cost-guard alarm fires."
  default     = 6
}

variable "alarm_email" {
  type        = string
  description = "Optional email to subscribe to the alarms SNS topic. Empty disables."
  default     = ""
}

variable "root_volume_size_gb" {
  type        = number
  description = "Size of the runtime instance root EBS volume in GB."
  default     = 200
}

variable "bootstrap_via_user_data" {
  type        = bool
  description = "If true, render cloud-init user_data that installs the CW agent, /etc/environment, and the aws-llm-bootstrap systemd unit. Required true for the autoscaling pipeline — workers can only run if user_data wires up the bootstrap unit on every fresh launch. Disable temporarily only when interactively iterating on bootstrap-shim.sh against a manually-launched instance."
  default     = true
}
