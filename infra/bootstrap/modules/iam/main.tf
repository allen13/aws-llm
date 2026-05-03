# Wraps the actions from the AWS-managed AmazonEC2ContainerRegistryReadOnly policy.
data "aws_iam_policy_document" "dlc_pull" {
  statement {
    sid    = "ECRDLCPull"
    effect = "Allow"
    actions = [
      "ecr:GetAuthorizationToken",
      "ecr:BatchCheckLayerAvailability",
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchGetImage",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "dlc_pull" {
  name        = "aws-llm-dlc-pull"
  description = "Pull AWS Deep Learning Containers (vLLM DLC) from in-region ECR."
  policy      = data.aws_iam_policy_document.dlc_pull.json
}

# Read model weights from models/, write per-run artifacts under experiments/.
data "aws_iam_policy_document" "artifacts_rw" {
  statement {
    sid       = "GetModels"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${var.artifacts_bucket_arn}/models/*"]
  }

  statement {
    sid       = "PutExperiments"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${var.artifacts_bucket_arn}/experiments/*"]
  }

  statement {
    sid       = "ListBucketScopedPrefixes"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [var.artifacts_bucket_arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values = [
        "models/*",
        "experiments/*",
      ]
    }
  }
}

resource "aws_iam_policy" "artifacts_rw" {
  name        = "aws-llm-artifacts-rw"
  description = "Read models/, write experiments/ on the aws-llm-artifacts bucket."
  policy      = data.aws_iam_policy_document.artifacts_rw.json
}

# R/W on the aws_llm S3 Tables namespace + the underlying storage prefix.
#
# pyiceberg's REST catalog client (used by extract_lib.iceberg_writer)
# walks the bucket → namespace → table hierarchy on connect AND issues
# `s3tables:CreateTable` against the bucket-level ARN (not the namespace
# ARN). We grant the full RW set on bucket + descendants in one
# statement; the runtime role is already scoped to a single instance.
data "aws_iam_policy_document" "tables_rw" {
  statement {
    sid    = "S3TablesNamespaceRW"
    effect = "Allow"
    # The runtime role is scoped to a single EC2 instance writing into a
    # single S3 Tables bucket that we own. pyiceberg's REST catalog calls
    # many s3tables actions across the bucket → namespace → table
    # hierarchy (CreateTable, UpdateTableMetadataLocation, …); rather
    # than enumerate every one (and chase new ones on each pyiceberg
    # bump), grant the full namespace within this bucket.
    actions = ["s3tables:*"]
    resources = [
      var.tables_bucket_arn,
      "${var.tables_bucket_arn}/*",
    ]
  }

  statement {
    sid    = "TableBucketStorageRW"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
    ]
    resources = ["${var.tables_bucket_arn}/*"]
  }
}

resource "aws_iam_policy" "tables_rw" {
  name        = "aws-llm-tables-rw"
  description = "Read/write the aws_llm namespace inside the aws-llm-tables S3 Tables bucket."
  policy      = data.aws_iam_policy_document.tables_rw.json
}
