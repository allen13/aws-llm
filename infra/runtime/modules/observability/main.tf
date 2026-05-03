resource "aws_cloudwatch_log_group" "runtime" {
  name              = "/aws/aws-llm/runtime"
  retention_in_days = var.log_retention_days

  tags = {
    Project = "aws-llm"
  }
}

# Ships the Python extraction logs from /var/log/aws-llm/*.log on the EC2
# instance via the CloudWatch agent installed in user_data.
# `make logs` tails this group.
resource "aws_cloudwatch_log_group" "extract" {
  name              = "/aws/aws-llm/extract"
  retention_in_days = var.log_retention_days

  tags = {
    Project = "aws-llm"
  }
}

# Ships /var/log/cloud-init-output.log so first-boot user_data failures
# are visible without SSH-ing the box.
resource "aws_cloudwatch_log_group" "cloud_init" {
  name              = "/aws/aws-llm/cloud-init"
  retention_in_days = var.log_retention_days

  tags = {
    Project = "aws-llm"
  }
}

resource "aws_sns_topic" "alarms" {
  name = "aws-llm-runtime-alarms"

  tags = {
    Project = "aws-llm"
  }
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alarm_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

# Cost-guard alarm: an instance staying InService past the worker's
# self-terminate cap (~50 min) suggests the worker is wedged. We watch
# AutoScalingGroupDesiredCapacity ≥ 1 sustained over the threshold.
# Period is 60 s (the AWS/AutoScaling resolution); evaluation_periods *
# 60 s = uptime_alarm_threshold_minutes minutes.
#
# This replaces the prior single-instance CPUUtilization heartbeat which
# could not be expressed without an instance_id; under the ASG, instance
# IDs are ephemeral and dimensioning by ASG name is the right shape.
resource "aws_cloudwatch_metric_alarm" "asg_uptime" {
  alarm_name          = "aws-llm-runtime-asg-uptime"
  alarm_description   = "Runtime ASG has had an instance InService for more than ${var.uptime_alarm_threshold_minutes} minutes — exceeds the worker's self-terminate cap. Investigate."
  namespace           = "AWS/AutoScaling"
  metric_name         = "GroupInServiceInstances"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = var.uptime_alarm_threshold_minutes
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    AutoScalingGroupName = var.asg_name
  }

  alarm_actions = [aws_sns_topic.alarms.arn]
  ok_actions    = [aws_sns_topic.alarms.arn]

  tags = {
    Project = "aws-llm"
  }
}
