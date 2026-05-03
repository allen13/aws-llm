provider "aws" {
  region = var.region
}

data "terraform_remote_state" "bootstrap" {
  backend = "s3"
  config = {
    bucket = "aws-llm-artifacts"
    key    = "state/bootstrap/terraform.tfstate"
    region = "us-east-1"
  }
}

# Deep Learning Base GPU AMI (Ubuntu 22.04), latest, resolved via SSM.
# AWS publishes the parameter under `.../latest/ami-id` (NOT image_id).
data "aws_ssm_parameter" "dl_base_gpu_ami" {
  name = "/aws/service/deeplearning/ami/x86_64/base-oss-nvidia-driver-gpu-ubuntu-22.04/latest/ami-id"
}

locals {
  artifacts_bucket_name = data.terraform_remote_state.bootstrap.outputs.artifacts_bucket_name
  tables_bucket_arn     = data.terraform_remote_state.bootstrap.outputs.tables_bucket_arn
  tables_namespace      = data.terraform_remote_state.bootstrap.outputs.tables_namespace
  jobs_queue_url        = data.terraform_remote_state.bootstrap.outputs.jobs_queue_url
  jobs_queue_arn        = data.terraform_remote_state.bootstrap.outputs.jobs_queue_arn
  asg_name              = data.terraform_remote_state.bootstrap.outputs.runtime_asg_name
  state_volume_id       = data.terraform_remote_state.bootstrap.outputs.state_volume_id
  state_volume_arn      = data.terraform_remote_state.bootstrap.outputs.state_volume_arn

  user_data = var.bootstrap_via_user_data ? templatefile("${path.module}/user_data.tftpl", {
    model_s3_uri          = var.model_s3_uri
    region                = var.region
    vllm_image_uri        = var.vllm_image_uri
    max_model_len         = var.max_model_len
    max_num_seqs          = var.max_num_seqs
    enable_thinking       = var.enable_thinking
    artifacts_bucket_name = local.artifacts_bucket_name
    tables_bucket_arn     = local.tables_bucket_arn
    tables_namespace      = local.tables_namespace
    jobs_queue_url        = local.jobs_queue_url
    asg_name              = local.asg_name
    state_volume_id       = local.state_volume_id
  }) : ""
}

module "network" {
  source            = "./modules/network"
  availability_zone = var.availability_zone
}

# --------------------------------------------------------------------------
# Configuration management: bootstrap-shim.sh + Ansible playbook tarball
# --------------------------------------------------------------------------
# user_data registers aws-llm-bootstrap.service which fetches and runs
# bootstrap-shim.sh. The shim apt-installs ansible (if absent), creates
# /opt/ansible-venv with boto3+docker, and exec's /opt/aws-llm-ansible/run.sh.
# run.sh is the one-true entrypoint for both the systemd-unit path AND the
# */5 cron the playbook installs (ansible_cron role).
#
# Pushing updates: `make sync-ansible` re-bundles the tarball; the cron
# picks it up within 5 min via the etag-marker check in run.sh.

# 1. The shim itself — small, stable, rarely edited.
resource "aws_s3_object" "bootstrap_shim" {
  bucket       = local.artifacts_bucket_name
  key          = "scripts/bootstrap-shim.sh"
  source       = "${path.module}/bootstrap-shim.sh"
  etag         = filemd5("${path.module}/bootstrap-shim.sh")
  content_type = "text/x-shellscript"
}

# 2. The Ansible playbook tarball. archive_file zips infra/runtime/ansible/
# at plan time; the etag-based aws_s3_object resource updates only when the
# zipped content changes.
data "archive_file" "ansible_playbook" {
  type        = "zip"
  source_dir  = "${path.module}/ansible"
  output_path = "${path.module}/build/ansible-playbook.zip"
}

# Convert .zip → .tar.gz at apply time; keeps the on-instance pull/extract
# pattern uniform with extract.tgz (which is gzip too). archive_file doesn't
# emit .tar.gz directly, so we use a null_resource shim that runs `tar`
# whenever the source dir's hash changes.
resource "null_resource" "ansible_tarball" {
  triggers = {
    archive_sha = data.archive_file.ansible_playbook.output_base64sha256
  }
  provisioner "local-exec" {
    command = <<-EOT
      tar --exclude=__pycache__ --exclude='*.retry' \
          -czf "${path.module}/build/ansible-playbook.tgz" \
          -C "${path.module}/ansible" .
    EOT
  }
}

resource "aws_s3_object" "ansible_tarball" {
  bucket       = local.artifacts_bucket_name
  key          = "scripts/ansible-playbook.tgz"
  source       = "${path.module}/build/ansible-playbook.tgz"
  content_type = "application/gzip"

  # The local-exec creates the tarball; its hash is the trigger we want
  # terraform to follow.
  etag = data.archive_file.ansible_playbook.output_md5

  depends_on = [null_resource.ansible_tarball]
}

module "compute" {
  source = "./modules/compute"

  region            = var.region
  instance_type     = var.instance_type
  use_spot          = var.use_spot
  spot_max_price    = var.spot_max_price
  availability_zone = var.availability_zone

  ami_id            = data.aws_ssm_parameter.dl_base_gpu_ami.value
  subnet_id         = module.network.subnet_id
  security_group_id = module.network.security_group_id
  user_data         = local.user_data

  dlc_pull_policy_arn     = data.terraform_remote_state.bootstrap.outputs.dlc_pull_policy_arn
  artifacts_rw_policy_arn = data.terraform_remote_state.bootstrap.outputs.artifacts_rw_policy_arn
  tables_rw_policy_arn    = data.terraform_remote_state.bootstrap.outputs.tables_rw_policy_arn

  root_volume_size_gb = var.root_volume_size_gb

  jobs_queue_arn   = local.jobs_queue_arn
  asg_name         = local.asg_name
  state_volume_id  = local.state_volume_id
  state_volume_arn = local.state_volume_arn
}

module "observability" {
  source = "./modules/observability"

  asg_name                     = local.asg_name
  uptime_alarm_threshold_hours = var.uptime_alarm_threshold_hours
  alarm_email                  = var.alarm_email
}
