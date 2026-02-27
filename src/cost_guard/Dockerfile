# Dockerfile for AWS Lambda cost guard services
# Build from project root:
#   docker build --platform linux/amd64 -f src/cost_guard/Dockerfile -t cost-guard-lambda .
FROM public.ecr.aws/lambda/python:3.11

# Copy requirements and install runtime dependencies.
COPY src/cost_guard/requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r requirements.txt

# Copy the cost_guard package.
COPY src/cost_guard/ ${LAMBDA_TASK_ROOT}/cost_guard/

# Default command; per-function command override is set in CloudFormation ImageConfig.
CMD [ "cost_guard.monitor_handler.lambda_handler" ]

