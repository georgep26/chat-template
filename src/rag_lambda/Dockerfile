# Dockerfile for AWS Lambda RAG application
# Build from project root: docker build -f src/rag_lambda/Dockerfile -t rag-lambda .
FROM public.ecr.aws/lambda/python:3.11

# Install libpq for psycopg and build tools for compiling packages like numpy
RUN yum -y install postgresql-devel gcc gcc-c++ make && yum clean all

# Copy requirements file
COPY src/rag_lambda/requirements.txt ${LAMBDA_TASK_ROOT}/

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code maintaining package structure
# The code uses relative imports, so we maintain the rag_lambda package structure
COPY src/rag_lambda ${LAMBDA_TASK_ROOT}/rag_lambda

# Copy utils directory (needed for imports like ..utils.aws_utils)
# The code uses relative imports from ..utils, so copy to same level as rag_lambda
COPY src/utils ${LAMBDA_TASK_ROOT}/utils

# Copy config directory (needed for app configuration)
# The code expects config at ../config relative to rag_lambda modules
COPY config ${LAMBDA_TASK_ROOT}/config

# Set the CMD to your handler (using package path)
CMD [ "rag_lambda.main.lambda_handler" ]

