data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  athena_results_prefix = "athena-results/"
  athena_results_arn    = "${var.artifacts_bucket_arn}/athena-results/*"
  athena_output_uri     = "s3://${var.artifacts_bucket_name}/${local.athena_results_prefix}"

  glue_catalog_arn  = "arn:${data.aws_partition.current.partition}:glue:${var.region}:${data.aws_caller_identity.current.account_id}:catalog"
  glue_database_arn = "arn:${data.aws_partition.current.partition}:glue:${var.region}:${data.aws_caller_identity.current.account_id}:database/${var.tables_namespace}"
  glue_tables_arn   = "arn:${data.aws_partition.current.partition}:glue:${var.region}:${data.aws_caller_identity.current.account_id}:table/${var.tables_namespace}/*"
}

resource "aws_athena_workgroup" "aws_llm" {
  name        = "aws-llm"
  state       = "ENABLED"
  description = "Workgroup for read-only Athena queries against S3 Tables (Iceberg) for aws-llm."

  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = true
    bytes_scanned_cutoff_per_query     = var.athena_query_scan_limit_bytes
    engine_version {
      selected_engine_version = "Athena engine version 3"
    }

    result_configuration {
      output_location = local.athena_output_uri

      encryption_configuration {
        encryption_option = "SSE_S3"
      }
    }
  }
}

# Lambda execution role + inline policy
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

resource "aws_iam_role" "query_lambda" {
  name               = "aws-llm-table-query-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "query_lambda" {
  statement {
    sid    = "AthenaWorkgroup"
    effect = "Allow"
    actions = [
      "athena:StartQueryExecution",
      "athena:GetQueryExecution",
      "athena:GetQueryResults",
      "athena:StopQueryExecution",
    ]
    resources = [aws_athena_workgroup.aws_llm.arn]
  }

  statement {
    sid    = "GlueCatalogRead"
    effect = "Allow"
    actions = [
      "glue:GetTable",
      "glue:GetDatabase",
      "glue:GetPartitions",
    ]
    resources = [
      local.glue_catalog_arn,
      local.glue_database_arn,
      local.glue_tables_arn,
    ]
  }

  statement {
    sid    = "S3TablesRead"
    effect = "Allow"
    actions = [
      "s3tables:GetTableData",
      "s3tables:GetTableMetadata",
      "s3tables:GetNamespace",
    ]
    resources = [
      var.tables_namespace_arn,
      "${var.tables_namespace_arn}/*",
    ]
  }

  statement {
    sid    = "S3TableBucketStorageRead"
    effect = "Allow"
    actions = [
      "s3:GetObject",
    ]
    resources = ["${var.tables_bucket_arn}/*"]
  }

  statement {
    sid    = "AthenaResultsRead"
    effect = "Allow"
    actions = [
      "s3:GetObject",
    ]
    resources = [local.athena_results_arn]
  }

  statement {
    sid    = "AthenaResultsWrite"
    effect = "Allow"
    actions = [
      "s3:PutObject",
    ]
    resources = [local.athena_results_arn]
  }

  statement {
    sid    = "CloudWatchLogsBasic"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["arn:${data.aws_partition.current.partition}:logs:${var.region}:${data.aws_caller_identity.current.account_id}:*"]
  }
}

resource "aws_iam_role_policy" "query_lambda" {
  name   = "aws-llm-table-query-inline"
  role   = aws_iam_role.query_lambda.id
  policy = data.aws_iam_policy_document.query_lambda.json
}

# Build the Lambda zip from lambda/query/ at the repo root.
# path.module is infra/bootstrap/modules/query/, so ../../../../lambda/query resolves to repo-root lambda/query/.
data "archive_file" "query_lambda" {
  type        = "zip"
  source_dir  = "${path.module}/../../../../lambda/query"
  output_path = "${path.module}/build/query_lambda.zip"
}

resource "aws_lambda_function" "aws_llm_table_query" {
  function_name    = "aws-llm-table-query"
  description      = "Read-only Athena query interface for aws-llm S3 Tables."
  role             = aws_iam_role.query_lambda.arn
  runtime          = "python3.12"
  handler          = "handler.handler"
  memory_size      = 256
  timeout          = 60
  filename         = data.archive_file.query_lambda.output_path
  source_code_hash = data.archive_file.query_lambda.output_base64sha256

  environment {
    variables = {
      ATHENA_WORKGROUP       = aws_athena_workgroup.aws_llm.name
      ATHENA_DATABASE        = var.tables_namespace
      ATHENA_OUTPUT_LOCATION = local.athena_output_uri
    }
  }
}
