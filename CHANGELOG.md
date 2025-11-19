# Changelog

All notable changes to this project will be documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).


## [1.1.0] - 2025-11-18
### Summary
Baseline LangGraph RAG application with AWS Bedrock Knowledge Base integration, PostgreSQL database deployment, and enhanced RAG pipeline capabilities. T

### Added
- AWS Bedrock Knowledge Base integration with deployment scripts and SQL setup for embeddings table
- AWS Secrets Manager integration for secure database credential retrieval
- CloudFormation templates and deployment scripts for Aurora Serverless v2 PostgreSQL database
- CloudFormation templates and deployment scripts for S3 bucket to store knowledge base documents
- CloudFormation templates for RAG Chat Lambda function and IAM execution role
- Retrieval node implementation for RAG pipeline with support for retrieval filters
- Enhanced message storage with metadata support
- Configuration support for different models in graph execution
- LocalSQLite memory backend support (placeholder implementation)
- Configuration utility module for reading JSON and YAML files from local filesystem or S3
- Logging utility module for improved logging management
- Command-line interface for local testing of Lambda handler
- Pull request template to standardize submission process
- Issue templates for bug reports, feature requests, and general tasks
- Example IAM role for Lambda execution with documentation
- AGENT.md with instructions for using the Python template repository
- CONTRIBUTING.md with guidelines for onboarding, development workflow, and pull requests
- Code standards documentation
- Development process documentation with GitHub project integration
- Copilot version of intro prompt



## [1.0.0] - 2025-10-30
### Added
- Initial template repository structure.
- First pass at RAG
