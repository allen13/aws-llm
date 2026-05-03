# =============================================================================
# Terraform S3 backend for the RUNTIME stack.
#
# >>> Use the SAME bucket as the bootstrap backend (infra/bootstrap/backend.tf).
# >>> The state key prefix below puts runtime state in a separate object so
#     the two stacks don't collide.
# =============================================================================
terraform {
  backend "s3" {
    bucket       = "REPLACE-ME-aws-llm-tfstate"
    key          = "state/runtime/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}
