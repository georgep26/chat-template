# Network Deployment Guide

This guide explains how to deploy the VPC network infrastructure for the RAG chat application.

## Overview

The network deployment creates a complete VPC setup with:
- **VPC** with public and private subnets across 2 availability zones
- **Security Groups** for Lambda and Database with proper access rules
- **VPC Endpoints** for Bedrock, Secrets Manager, and S3
- **Optional NAT Gateway** for additional outbound internet access

## Quick Start

```bash
# Deploy network to development environment
./scripts/deploy/deploy_network.sh dev deploy

# Deploy without NAT Gateway (cost-optimized)
./scripts/deploy/deploy_network.sh dev deploy --no-nat-gateway

# Check deployment status
./scripts/deploy/deploy_network.sh dev status
```

## What Gets Created

### Network Components

1. **VPC** (`10.0.0.0/16` by default)
   - DNS hostnames and support enabled
   - Tagged with project and environment

2. **Subnets** (4 total, 2 per AZ)
   - **Public Subnets** (2): `10.0.0.0/24`, `10.0.1.0/24`
     - Used for NAT Gateway (if enabled)
     - Auto-assign public IP enabled
   - **Private Subnets** (2): `10.0.2.0/24`, `10.0.3.0/24`
     - Used for Lambda and RDS
     - No public IP assignment

3. **Internet Gateway**
   - Attached to VPC
   - Enables public subnet internet access

4. **NAT Gateway** (optional)
   - Provides outbound internet access for private subnets
   - Uses Elastic IP
   - Can be disabled with `--no-nat-gateway` flag

5. **Route Tables**
   - **Public Route Table**: Routes `0.0.0.0/0` → Internet Gateway
   - **Private Route Table**: Routes `0.0.0.0/0` → NAT Gateway (if enabled)

### Security Groups

1. **Lambda Security Group**
   - **Egress Rules**:
     - HTTPS (443) to `0.0.0.0/0` - for AWS services
     - PostgreSQL (5432) to VPC CIDR - for database access
   - Used by Lambda functions

2. **Database Security Group**
   - **Ingress Rules**:
     - PostgreSQL (5432) from Lambda Security Group
   - Used by Aurora PostgreSQL database

### VPC Endpoints

1. **Bedrock VPC Endpoint** (Interface)
   - Service: `com.amazonaws.<region>.bedrock-runtime`
   - Allows Lambda to access Bedrock without internet
   - Deployed in both private subnets for HA

2. **Secrets Manager VPC Endpoint** (Interface)
   - Service: `com.amazonaws.<region>.secretsmanager`
   - Allows Lambda to retrieve database credentials
   - Deployed in both private subnets for HA

3. **S3 VPC Endpoint** (Gateway)
   - Service: `com.amazonaws.<region>.s3`
   - Allows Lambda to access S3 buckets
   - Free (no hourly charges)

## Security Group Rules Explained

### Lambda → Database Access

The Lambda security group allows outbound traffic to port 5432 (PostgreSQL) within the VPC CIDR. The database security group allows inbound traffic from the Lambda security group on port 5432. This creates a secure connection between Lambda and RDS.

### Lambda → AWS Services Access

Lambda can access AWS services (Bedrock, Secrets Manager, S3) through:
1. **VPC Endpoints** (preferred): Private connectivity, lower cost
2. **NAT Gateway** (if enabled): Internet-based access, higher cost

## Cost Considerations

See `infra/cloudformation/NETWORK_COST_ESTIMATE.md` for detailed cost breakdown.

**Quick Summary**:
- **With NAT Gateway**: ~$47/month + data transfer
- **Without NAT Gateway**: ~$15/month + data transfer

**Recommendation**: Use VPC endpoints without NAT Gateway for cost optimization.

## Integration with Other Stacks

The network stack outputs are automatically detected by other deployment scripts:

### Database Deployment
```bash
# Will auto-detect VPC and subnets from network stack
./scripts/deploy/deploy_chat_template_db.sh dev deploy --master-password <password>
```

### Lambda Deployment
```bash
# Will auto-detect VPC, subnets, and security groups from network stack
./scripts/deploy/deploy_rag_lambda.sh dev deploy
```

## Deployment Order

1. **Deploy Network** (this script)
   ```bash
   ./scripts/deploy/deploy_network.sh dev deploy
   ```

2. **Deploy Database**
   ```bash
   ./scripts/deploy/deploy_chat_template_db.sh dev deploy --master-password <password>
   ```

3. **Deploy Lambda**
   ```bash
   ./scripts/deploy/deploy_rag_lambda.sh dev deploy
   ```

## Customization

### Custom VPC CIDR
```bash
./scripts/deploy/deploy_network.sh dev deploy --vpc-cidr 10.1.0.0/16
```

**Note**: If you change the VPC CIDR, you'll need to update the subnet CIDR blocks in `vpc_template.yaml` to match.

### Disable NAT Gateway
```bash
./scripts/deploy/deploy_network.sh dev deploy --no-nat-gateway
```

### Custom Region
```bash
./scripts/deploy/deploy_network.sh dev deploy --region us-west-2
```

## Troubleshooting

### Stack Creation Fails
- Check AWS CLI credentials: `aws sts get-caller-identity`
- Verify region is correct
- Check CloudFormation console for detailed error messages

### Lambda Can't Access Bedrock
- Verify VPC endpoints are created: `aws ec2 describe-vpc-endpoints`
- Check Lambda security group allows HTTPS (443) outbound
- Verify Lambda is in private subnets with VPC endpoints

### Lambda Can't Access Database
- Verify database security group allows inbound from Lambda security group
- Check Lambda security group allows outbound to port 5432
- Verify both are in the same VPC

### High Costs
- Consider disabling NAT Gateway if not needed
- Monitor data transfer in CloudWatch
- Review VPC endpoint usage

## Stack Outputs

After deployment, the stack provides these outputs:

- `VpcId`: VPC ID
- `PublicSubnetIds`: Comma-separated public subnet IDs
- `PrivateSubnetIds`: Comma-separated private subnet IDs
- `LambdaSecurityGroupId`: Security group for Lambda
- `DBSecurityGroupId`: Security group for database
- `BedrockVpcEndpointId`: VPC endpoint for Bedrock
- `SecretsManagerVpcEndpointId`: VPC endpoint for Secrets Manager
- `S3VpcEndpointId`: VPC endpoint for S3

View outputs:
```bash
./scripts/deploy/deploy_network.sh dev status
```

## Cleanup

To delete the network stack:

```bash
./scripts/deploy/deploy_network.sh dev delete
```

**Warning**: This will delete all network resources. Make sure to delete dependent resources (database, Lambda) first.

## Additional Resources

- [AWS VPC Documentation](https://docs.aws.amazon.com/vpc/)
- [VPC Endpoints Documentation](https://docs.aws.amazon.com/vpc/latest/privatelink/)
- [Security Groups Documentation](https://docs.aws.amazon.com/vpc/latest/userguide/security-groups.html)
- Cost Estimate: `infra/cloudformation/NETWORK_COST_ESTIMATE.md`

