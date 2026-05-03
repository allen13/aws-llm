data "aws_iam_policy_document" "ec2_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "instance" {
  name               = "aws-llm-runtime-instance"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json

  tags = {
    Project = "aws-llm"
  }
}

resource "aws_iam_role_policy_attachment" "dlc_pull" {
  role       = aws_iam_role.instance.name
  policy_arn = var.dlc_pull_policy_arn
}

resource "aws_iam_role_policy_attachment" "artifacts_rw" {
  role       = aws_iam_role.instance.name
  policy_arn = var.artifacts_rw_policy_arn
}

# The bootstrap-stack `artifacts-rw` policy only covers `models/*` and
# `experiments/*`. Runtime needs to additionally read scripts/ (bootstrap-shim.sh),
# code/ (extract.tgz), datasets/, and write diagnostics/ (failure logs).
# Kept as an inline policy on the runtime role rather than amended into the
# bootstrap stack to keep concerns separated.
resource "aws_iam_role_policy" "artifacts_runtime_extras" {
  name = "artifacts-runtime-extras"
  role = aws_iam_role.instance.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "GetRuntimeArtifacts"
        Effect = "Allow"
        Action = "s3:GetObject"
        Resource = [
          "arn:aws:s3:::aws-llm-artifacts/scripts/*",
          "arn:aws:s3:::aws-llm-artifacts/code/*",
          "arn:aws:s3:::aws-llm-artifacts/datasets/*",
          "arn:aws:s3:::aws-llm-artifacts/jobs/inbox/*",
        ]
      },
      {
        Sid    = "PutDiagnostics"
        Effect = "Allow"
        Action = "s3:PutObject"
        Resource = [
          "arn:aws:s3:::aws-llm-artifacts/diagnostics/*",
          # Optional: if the worker writes "processed" markers to jobs/done/*
          "arn:aws:s3:::aws-llm-artifacts/jobs/done/*",
        ]
      },
      {
        Sid      = "ListRuntimePrefixes"
        Effect   = "Allow"
        Action   = "s3:ListBucket"
        Resource = "arn:aws:s3:::aws-llm-artifacts"
        Condition = {
          StringLike = {
            "s3:prefix" = ["scripts/*", "code/*", "datasets/*", "diagnostics/*", "jobs/*"]
          }
        }
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "tables_rw" {
  role       = aws_iam_role.instance.name
  policy_arn = var.tables_rw_policy_arn
}

# SSM Session Manager + RunCommand. Required for `make exec` / `make run`
# / `make sync-code` (all the inner-loop ops use ssm:SendCommand).
resource "aws_iam_role_policy_attachment" "ssm_managed_core" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# CloudWatch Logs agent push permission. Lets the CW agent installed in
# user_data ship /var/log/aws-llm/* and cloud-init logs to the log
# groups defined in the observability module.
resource "aws_iam_role_policy_attachment" "cloudwatch_agent" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy"
}

resource "aws_iam_instance_profile" "instance" {
  name = "aws-llm-runtime-instance"
  role = aws_iam_role.instance.name
}

resource "aws_launch_template" "vllm" {
  name_prefix   = "aws-llm-runtime-"
  image_id      = var.ami_id
  instance_type = var.instance_type

  iam_instance_profile {
    name = aws_iam_instance_profile.instance.name
  }

  vpc_security_group_ids = [var.security_group_id]

  metadata_options {
    http_tokens                 = "required"
    http_endpoint               = "enabled"
    http_put_response_hop_limit = 2
  }

  user_data = var.user_data != "" ? base64encode(var.user_data) : null

  block_device_mappings {
    device_name = "/dev/sda1"
    ebs {
      volume_size           = var.root_volume_size_gb
      volume_type           = "gp3"
      delete_on_termination = true
      encrypted             = true
    }
  }

  tag_specifications {
    resource_type = "instance"
    tags = {
      Name    = "aws-llm-runtime"
      Project = "aws-llm"
    }
  }

  tag_specifications {
    resource_type = "volume"
    tags = {
      Name    = "aws-llm-runtime-root"
      Project = "aws-llm"
    }
  }

  tags = {
    Project = "aws-llm"
  }
}

# ----------------------------------------------------------------------------
# Auto-Scaling Group — replaces the prior single aws_instance.vllm.
#
# desired=0 by default; the dispatcher Lambda (bootstrap stack) sets desired=1
# when SQS queue depth ≥ threshold or an SNS force-scale event arrives. The
# worker on the instance self-terminates via TerminateInstanceInAutoScalingGroup
# (decrement=true) at the wall-clock or idle cap, returning desired→0.
#
# Spot is intentionally not wired here: v1 is on-demand only. When we
# revisit, use mixed_instances_policy + spot_max_price + on-demand-base on
# the ASG; the launch_template stays as the launch spec.
#
# Block-device sizing lives in the launch template (block_device_mappings),
# so it's not repeated here. Tags propagate to instances via the LT's
# tag_specifications block.
# ----------------------------------------------------------------------------
resource "aws_autoscaling_group" "runtime" {
  name = var.asg_name

  min_size         = 0
  max_size         = 1
  desired_capacity = 0

  vpc_zone_identifier = [var.subnet_id]

  launch_template {
    id      = aws_launch_template.vllm.id
    version = "$Latest"
  }

  # Don't let dispatcher SetDesiredCapacity collide with terraform plans
  # noticing a transient desired=1 mid-run. The capacity is operationally
  # managed; terraform owns only the floor and ceiling.
  lifecycle {
    ignore_changes = [desired_capacity]
  }

  tag {
    key                 = "Name"
    value               = "aws-llm-runtime"
    propagate_at_launch = true
  }

  tag {
    key                 = "Project"
    value               = "aws-llm"
    propagate_at_launch = true
  }
}

# ----------------------------------------------------------------------------
# Worker IAM additions — extend the existing instance role.
#
# The existing role already has S3, ECR, S3 Tables, SSM, CloudWatch Logs.
# Add: SQS receive/delete/heartbeat against the jobs queue, and ASG
# self-terminate. We use TerminateInstanceInAutoScalingGroup with
# ShouldDecrementDesiredCapacity=true rather than ec2:TerminateInstances
# so the ASG doesn't immediately replace the dying worker.
# ----------------------------------------------------------------------------
data "aws_iam_policy_document" "sqs_worker" {
  statement {
    sid    = "JobsQueueDrain"
    effect = "Allow"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:ChangeMessageVisibility",
      "sqs:GetQueueAttributes",
      "sqs:GetQueueUrl",
    ]
    resources = [var.jobs_queue_arn]
  }
}

resource "aws_iam_role_policy" "sqs_worker" {
  name   = "aws-llm-sqs-worker"
  role   = aws_iam_role.instance.id
  policy = data.aws_iam_policy_document.sqs_worker.json
}

data "aws_iam_policy_document" "asg_self_terminate" {
  statement {
    sid    = "SelfTerminateInASG"
    effect = "Allow"
    actions = [
      "autoscaling:TerminateInstanceInAutoScalingGroup",
      "autoscaling:DescribeAutoScalingInstances",
      "autoscaling:DescribeAutoScalingGroups",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "asg_self_terminate" {
  name   = "aws-llm-asg-self-terminate"
  role   = aws_iam_role.instance.id
  policy = data.aws_iam_policy_document.asg_self_terminate.json
}

# State-volume self-attach. DescribeVolumes is needed for the worker to
# discover the volume by tag (and check its current attachment state for
# idempotency); AttachVolume is the actual operation, scoped to the volume
# ARN + the instance acting on its own behalf.
data "aws_iam_policy_document" "state_volume_attach" {
  statement {
    sid    = "DescribeVolumesAndInstancesAccountWide"
    effect = "Allow"
    actions = [
      "ec2:DescribeVolumes",
      # community.aws.ec2_vol's instance-attachment path calls DescribeInstances
      # to validate the target. Neither action supports resource-level scoping.
      "ec2:DescribeInstances",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "AttachStateVolume"
    effect = "Allow"
    actions = [
      "ec2:AttachVolume",
      "ec2:DetachVolume",
    ]
    # Two resources are required: the volume itself and the instance the
    # volume is being attached to. Using *:instance/* and the specific
    # volume ARN keeps the volume tightly scoped while letting the
    # principal target any instance it controls (it can only target
    # itself in practice — IAM doesn't restrict beyond the resource).
    resources = [
      var.state_volume_arn,
      "arn:aws:ec2:*:*:instance/*",
    ]
  }
}

resource "aws_iam_role_policy" "state_volume_attach" {
  name   = "aws-llm-state-volume-attach"
  role   = aws_iam_role.instance.id
  policy = data.aws_iam_policy_document.state_volume_attach.json
}
