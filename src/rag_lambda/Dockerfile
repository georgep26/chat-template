# Dockerfile for AWS Lambda RAG application
# Build from project root: docker build -f src/rag_lambda/Dockerfile -t rag-lambda .
FROM public.ecr.aws/lambda/python:3.11

# Copy requirements file
COPY src/rag_lambda/requirements.txt ${LAMBDA_TASK_ROOT}/

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code (including __init__.py files)
COPY src/rag_lambda/api ${LAMBDA_TASK_ROOT}/api
COPY src/rag_lambda/graph ${LAMBDA_TASK_ROOT}/graph
COPY src/rag_lambda/memory ${LAMBDA_TASK_ROOT}/memory
COPY src/rag_lambda/__init__.py ${LAMBDA_TASK_ROOT}/
COPY src/rag_lambda/main.py ${LAMBDA_TASK_ROOT}/

# Copy config directory (needed for app configuration)
# The code expects config at ../config relative to rag_lambda modules
COPY config ${LAMBDA_TASK_ROOT}/../config

# Set the CMD to your handler
CMD [ "main.lambda_handler" ]

