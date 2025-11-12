# Cloud Roles and Permissions

This directory contains definitions for cloud roles and permissions, such as IAM roles, IAM policies, and other access control configurations.

## Purpose

Any definition for cloud roles should be added here. This includes:
- IAM roles for applications, services, or users
- IAM policies and permission sets
- Service-specific role configurations (e.g., Lambda execution roles, ECS task roles)
- Cross-account access roles
- Role trust relationships

## Organization

Role definitions can be organized by:
- **Service**: Group roles by the AWS service they're used with (e.g., Lambda, ECS, EC2)
- **Environment**: Separate roles for different environments (dev, staging, prod)
- **Function**: Organize by the function or purpose of the role

## Integration

These role definitions can be:
- Referenced in your main CloudFormation or Terraform templates
- Deployed as standalone stacks/modules
- Included as reusable components across multiple projects

## Example

See `example-lambda-execution-role.yaml` for a complete example of an IAM role definition that can be used with AWS Lambda functions.