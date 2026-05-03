variable "asg_name" {
  type        = string
  description = "Name of the runtime Auto-Scaling Group for the cost-guard alarm dimension."
}

variable "uptime_alarm_threshold_minutes" {
  type        = number
  description = "Minutes of sustained InService before the cost-guard alarm fires. Must exceed the worker's self-terminate cap (50 min default); 70 gives a 20-min margin for warm-up plus drain."
  default     = 70
}

variable "uptime_alarm_threshold_hours" {
  type        = number
  description = "Deprecated; kept on the variable surface for backward compat with existing root-level wiring. Ignored by the ASG-based alarm."
  default     = 6
}

variable "alarm_email" {
  type        = string
  description = "Optional email to subscribe to the SNS alarms topic. Empty = no subscription."
  default     = ""
}

variable "log_retention_days" {
  type        = number
  description = "CloudWatch log retention in days."
  default     = 30
}
