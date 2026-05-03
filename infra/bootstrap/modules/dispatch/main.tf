data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition

  # Resource-level ARN form for autoscaling actions. The wildcard segment is
  # the ASG's per-account UUID, which we don't know at plan time; the ASG
  # name segment is the stable identity.
  asg_arn_pattern = "arn:${local.partition}:autoscaling:${var.region}:${local.account_id}:autoScalingGroup:*:autoScalingGroupName/${var.asg_name}"
}

# ----------------------------------------------------------------------------
# Dead-letter queue
# ----------------------------------------------------------------------------

resource "aws_sqs_queue" "jobs_dlq" {
  name                      = "${var.jobs_queue_name}-dlq"
  message_retention_seconds = 1209600 # 14 days, the maximum
  tags                      = { Project = "aws-llm" }
}

# ----------------------------------------------------------------------------
# Main jobs queue
# ----------------------------------------------------------------------------

resource "aws_sqs_queue" "jobs" {
  name                       = var.jobs_queue_name
  visibility_timeout_seconds = var.visibility_timeout_seconds
  message_retention_seconds  = 1209600 # 14 days
  receive_wait_time_seconds  = 20      # long-poll default

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.jobs_dlq.arn
    maxReceiveCount     = var.max_receive_count
  })

  tags = { Project = "aws-llm" }
}

# Allow S3 to deliver ObjectCreated events to this queue. The
# aws_s3_bucket_notification declared in the storage module is what actually
# wires the events; this policy is the queue-side acceptance grant. Scoped
# tightly to the artifacts bucket + this account.
data "aws_iam_policy_document" "jobs_queue" {
  statement {
    sid     = "AllowS3SendMessage"
    effect  = "Allow"
    actions = ["sqs:SendMessage"]
    principals {
      type        = "Service"
      identifiers = ["s3.amazonaws.com"]
    }
    resources = [aws_sqs_queue.jobs.arn]
    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [var.artifacts_bucket_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_sqs_queue_policy" "jobs" {
  queue_url = aws_sqs_queue.jobs.id
  policy    = data.aws_iam_policy_document.jobs_queue.json
}

# ----------------------------------------------------------------------------
# Force-scale SNS topic
# ----------------------------------------------------------------------------

resource "aws_sns_topic" "force_scale" {
  name = var.force_scale_topic_name
  tags = { Project = "aws-llm" }
}

# ----------------------------------------------------------------------------
# Dispatcher Lambda — role + inline policy
# ----------------------------------------------------------------------------

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "dispatcher" {
  name               = "aws-llm-dispatcher-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = { Project = "aws-llm" }
}

# Several autoscaling Describe* actions don't support resource-level
# permissions; SetDesiredCapacity does. SQS GetQueueAttributes is scoped to
# the jobs queue ARN. Logs is the standard Lambda basic-execution shape.
data "aws_iam_policy_document" "dispatcher" {
  statement {
    sid    = "AutoscalingDescribe"
    effect = "Allow"
    actions = [
      "autoscaling:DescribeAutoScalingGroups",
      "autoscaling:DescribeScalingActivities",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "AutoscalingScale"
    effect    = "Allow"
    actions   = ["autoscaling:SetDesiredCapacity"]
    resources = [local.asg_arn_pattern]
  }

  statement {
    sid       = "SqsRead"
    effect    = "Allow"
    actions   = ["sqs:GetQueueAttributes"]
    resources = [aws_sqs_queue.jobs.arn]
  }

  statement {
    sid    = "CloudWatchLogsBasic"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["arn:${local.partition}:logs:${var.region}:${local.account_id}:*"]
  }
}

resource "aws_iam_role_policy" "dispatcher" {
  name   = "aws-llm-dispatcher-inline"
  role   = aws_iam_role.dispatcher.id
  policy = data.aws_iam_policy_document.dispatcher.json
}

# ----------------------------------------------------------------------------
# Dispatcher Lambda — code + function
# ----------------------------------------------------------------------------

# path.module is infra/bootstrap/modules/dispatch/, so ../../../../lambda/dispatch
# resolves to repo-root lambda/dispatch/.
data "archive_file" "dispatcher" {
  type        = "zip"
  source_dir  = "${path.module}/../../../../lambda/dispatch"
  output_path = "${path.module}/build/dispatcher.zip"
}

resource "aws_lambda_function" "dispatcher" {
  function_name    = "aws-llm-dispatcher"
  description      = "Scales aws-llm-runtime ASG 0→1 when SQS depth ≥ threshold or SNS force-scale fires."
  role             = aws_iam_role.dispatcher.arn
  runtime          = "python3.12"
  handler          = "handler.handler"
  memory_size      = 256
  timeout          = 30
  filename         = data.archive_file.dispatcher.output_path
  source_code_hash = data.archive_file.dispatcher.output_base64sha256

  environment {
    variables = {
      ASG_NAME            = var.asg_name
      JOBS_QUEUE_URL      = aws_sqs_queue.jobs.id
      MIN_FILES_THRESHOLD = tostring(var.min_files_threshold)
    }
  }

  tags = { Project = "aws-llm" }
}

# ----------------------------------------------------------------------------
# EventBridge scheduled trigger
# ----------------------------------------------------------------------------

resource "aws_cloudwatch_event_rule" "dispatcher_schedule" {
  name                = "aws-llm-dispatcher-schedule"
  description         = "Periodic tick that lets the dispatcher Lambda check SQS depth and scale the ASG."
  schedule_expression = var.schedule_expression
  tags                = { Project = "aws-llm" }
}

resource "aws_cloudwatch_event_target" "dispatcher_schedule" {
  rule      = aws_cloudwatch_event_rule.dispatcher_schedule.name
  target_id = "dispatcher"
  arn       = aws_lambda_function.dispatcher.arn
}

resource "aws_lambda_permission" "allow_eventbridge" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.dispatcher.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.dispatcher_schedule.arn
}

# ----------------------------------------------------------------------------
# SNS force-scale trigger
# ----------------------------------------------------------------------------

resource "aws_sns_topic_subscription" "dispatcher" {
  topic_arn = aws_sns_topic.force_scale.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.dispatcher.arn
}

resource "aws_lambda_permission" "allow_sns" {
  statement_id  = "AllowSNSInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.dispatcher.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.force_scale.arn
}
