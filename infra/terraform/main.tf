# Terraform Infrastructure Configuration
# This file defines the AWS infrastructure for the Python template project

terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# Variables
variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Name of the project"
  type        = string
  default     = "python-template"
}

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "dev"
}

# S3 Bucket for application data
resource "aws_s3_bucket" "app_bucket" {
  bucket = "${var.project_name}-${var.environment}-data"

  tags = {
    Name        = "${var.project_name}-${var.environment}-data"
    Environment = var.environment
  }
}

# DynamoDB Table
resource "aws_dynamodb_table" "app_table" {
  name           = "${var.project_name}-${var.environment}-table"
  billing_mode   = "PAY_PER_REQUEST"
  hash_key       = "id"

  attribute {
    name = "id"
    type = "S"
  }

  tags = {
    Name        = "${var.project_name}-${var.environment}-table"
    Environment = var.environment
  }
}

# Outputs
output "s3_bucket_name" {
  description = "Name of the S3 bucket"
  value       = aws_s3_bucket.app_bucket.id
}

output "dynamodb_table_name" {
  description = "Name of the DynamoDB table"
  value       = aws_dynamodb_table.app_table.name
}
