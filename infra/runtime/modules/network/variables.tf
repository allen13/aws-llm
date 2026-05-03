variable "availability_zone" {
  type        = string
  description = "AZ for the public subnet."
}

variable "vpc_cidr" {
  type        = string
  description = "VPC CIDR block."
  default     = "10.20.0.0/16"
}

variable "subnet_cidr" {
  type        = string
  description = "Public subnet CIDR block."
  default     = "10.20.1.0/24"
}
