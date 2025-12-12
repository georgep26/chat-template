# Network Infrastructure Cost Estimate

This document provides cost estimates for the VPC network infrastructure created by `deploy_network.sh`.

## Monthly Cost Breakdown

### Free Resources
- **VPC**: $0/month
- **Subnets**: $0/month
- **Internet Gateway**: $0/month (charges only for data transfer)
- **Route Tables**: $0/month
- **Security Groups**: $0/month
- **S3 VPC Endpoint (Gateway)**: $0/month

### Paid Resources

#### Option 1: With NAT Gateway (Recommended for Flexibility)
| Resource | Monthly Cost | Notes |
|----------|--------------|-------|
| NAT Gateway | ~$32.40 | $0.045/hour × 24 hours × 30 days |
| Elastic IP (for NAT) | $0 | Free when attached to NAT Gateway |
| Bedrock VPC Endpoint (Interface) | ~$7.20 | $0.01/hour × 24 hours × 30 days |
| Secrets Manager VPC Endpoint (Interface) | ~$7.20 | $0.01/hour × 24 hours × 30 days |
| **Subtotal (Fixed)** | **~$46.80/month** | |
| Data Transfer (NAT Gateway) | Variable | $0.045/GB (first 10TB) |
| Data Transfer (VPC Endpoints) | Variable | $0.01/GB (first 10TB) |

**Total Estimated Monthly Cost: ~$47-60/month** (depending on data transfer)

#### Option 2: Without NAT Gateway (Cost-Optimized)
| Resource | Monthly Cost | Notes |
|----------|--------------|-------|
| Bedrock VPC Endpoint (Interface) | ~$7.20 | $0.01/hour × 24 hours × 30 days |
| Secrets Manager VPC Endpoint (Interface) | ~$7.20 | $0.01/hour × 24 hours × 30 days |
| **Subtotal (Fixed)** | **~$14.40/month** | |
| Data Transfer (VPC Endpoints) | Variable | $0.01/GB (first 10TB) |

**Total Estimated Monthly Cost: ~$15-25/month** (depending on data transfer)

## Cost Comparison

| Configuration | Fixed Monthly Cost | Best For |
|---------------|-------------------|----------|
| **With NAT Gateway** | ~$47/month | Maximum flexibility, access to any AWS service |
| **Without NAT Gateway** | ~$15/month | Cost-optimized, only access services with VPC endpoints |

## Data Transfer Costs

### NAT Gateway Data Transfer
- First 1 GB/month: Free
- Next 9.999 TB/month: $0.045 per GB
- Over 10 TB/month: $0.045 per GB

### VPC Endpoint Data Transfer
- First 1 GB/month: Free (per endpoint)
- Next 9.999 TB/month: $0.01 per GB
- Over 10 TB/month: $0.01 per GB

**Note**: Data transfer between VPC endpoints and AWS services in the same region is typically free or low-cost.

## Cost Optimization Recommendations

1. **Use VPC Endpoints Instead of NAT Gateway** (if possible)
   - Saves ~$32/month
   - Lower data transfer costs ($0.01/GB vs $0.045/GB)
   - More secure (traffic stays within AWS network)

2. **Disable NAT Gateway for Development**
   - Use `--no-nat-gateway` flag
   - Only enable for staging/production if needed

3. **Monitor Data Transfer**
   - Set up CloudWatch alarms for unexpected data transfer
   - Review AWS Cost Explorer regularly

4. **Use S3 Gateway Endpoint**
   - Already included (free)
   - No additional cost for S3 access

## Example Monthly Costs by Usage

### Low Usage (Development)
- 10 GB data transfer/month
- **With NAT**: ~$47 + (10 GB × $0.045) = **~$47.45/month**
- **Without NAT**: ~$14.40 + (10 GB × $0.01) = **~$14.50/month**

### Medium Usage (Staging)
- 100 GB data transfer/month
- **With NAT**: ~$47 + (100 GB × $0.045) = **~$51.50/month**
- **Without NAT**: ~$14.40 + (100 GB × $0.01) = **~$15.40/month**

### High Usage (Production)
- 1 TB data transfer/month
- **With NAT**: ~$47 + (1024 GB × $0.045) = **~$93/month**
- **Without NAT**: ~$14.40 + (1024 GB × $0.01) = **~$24.64/month**

## Additional Considerations

1. **Elastic IP**: Free when attached to NAT Gateway, but $0.005/hour (~$3.60/month) if not attached
2. **VPC Endpoint Availability Zones**: Interface endpoints are created in 2 AZs by default (cost is per endpoint, not per AZ)
3. **Regional Pricing**: Costs may vary slightly by AWS region
4. **Reserved Capacity**: Not applicable for these resources

## Cost Monitoring

To monitor your network costs:

1. **AWS Cost Explorer**
   - Filter by service: EC2 (for NAT Gateway), VPC (for endpoints)
   - Set up cost alerts

2. **CloudWatch Metrics**
   - Monitor NAT Gateway bytes transferred
   - Monitor VPC endpoint traffic

3. **AWS Budgets**
   - Set monthly budget alerts
   - Configure notifications

## Summary

**Recommended Configuration**: Use VPC endpoints without NAT Gateway for cost optimization (~$15/month fixed + data transfer).

**Maximum Flexibility**: Use NAT Gateway + VPC endpoints if you need access to services without VPC endpoints (~$47/month fixed + data transfer).

For most RAG chat applications, the VPC endpoints-only configuration (without NAT Gateway) is sufficient and provides significant cost savings.

