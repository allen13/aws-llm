output "vpc_id" {
  description = "VPC id."
  value       = aws_vpc.this.id
}

output "subnet_id" {
  description = "Public subnet id."
  value       = aws_subnet.public.id
}

output "security_group_id" {
  description = "Security group id for the vLLM instance."
  value       = aws_security_group.vllm.id
}

output "vpc_cidr" {
  description = "VPC CIDR block."
  value       = aws_vpc.this.cidr_block
}
