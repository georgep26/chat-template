# Dockerfile for AWS Lambda RAG application
# Build from project root: 
#   For x86_64: docker build --platform linux/amd64 -f src/rag_lambda/Dockerfile -t rag-lambda .
#   For arm64:  docker build --platform linux/arm64 -f src/rag_lambda/Dockerfile -t rag-lambda .
FROM public.ecr.aws/lambda/python:3.11

# Install libpq for psycopg and build tools for compiling packages like numpy
RUN yum -y install postgresql-devel gcc gcc-c++ make && yum clean all

# Copy requirements file
COPY src/rag_lambda/requirements.txt ${LAMBDA_TASK_ROOT}/

# Upgrade pip to ensure it can find prebuilt wheels
RUN pip install --upgrade pip

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code - copy contents of rag_lambda to a rag_lambda package
# Use trailing slashes to copy contents, not the directory itself
COPY src/rag_lambda/ ${LAMBDA_TASK_ROOT}/rag_lambda/

# Copy utils directory (needed for imports like ..utils.aws_utils)
COPY src/utils/ ${LAMBDA_TASK_ROOT}/utils/

# Copy config directory (needed for app configuration)
COPY config/ ${LAMBDA_TASK_ROOT}/config/

# Set the CMD to your handler
# The handler path is relative to LAMBDA_TASK_ROOT
CMD [ "rag_lambda.main.lambda_handler" ]

