# =============================================================================
# Terraform S3 backend for the BOOTSTRAP stack.
#
# >>> REPLACE the bucket name below before running `terraform init`.  <<<
#
# Recommended pattern:
#   1. Create an S3 bucket in your AWS account, e.g.
#        aws s3api create-bucket --bucket your-aws-llm-tfstate \
#                                --region us-east-1
#      Enable versioning + default encryption.
#   2. Replace the bucket value below with that bucket name.
#   3. `terraform init` — the state file will land at
#        s3://<your-bucket>/state/bootstrap/terraform.tfstate
#
# Alternatively, comment this block out entirely and Terraform will use a
# local state file (`terraform.tfstate`). Local state is fine for a
# single-operator dev environment but loses the locking + audit trail an
# S3 backend gives you.
# =============================================================================
terraform {
  backend "s3" {
    bucket       = "REPLACE-ME-aws-llm-tfstate"
    key          = "state/bootstrap/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}
