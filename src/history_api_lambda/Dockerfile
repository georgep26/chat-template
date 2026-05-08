# Dockerfile for AWS Lambda history API application.
# Build from project root:
#   For x86_64: docker build --platform linux/amd64 -f src/history_api_lambda/Dockerfile -t history-api-lambda .
#   For arm64:  docker build --platform linux/arm64 -f src/history_api_lambda/Dockerfile -t history-api-lambda .
FROM public.ecr.aws/lambda/python:3.11

# Install libpq for psycopg and a small build toolchain for any source wheels.
RUN yum -y install postgresql-devel gcc gcc-c++ make && yum clean all

COPY src/history_api_lambda/requirements.txt ${LAMBDA_TASK_ROOT}/

RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

# Application code (history_api_lambda package).
COPY src/history_api_lambda/ ${LAMBDA_TASK_ROOT}/history_api_lambda/

# Shared modules used by both Lambdas.
COPY src/auth/ ${LAMBDA_TASK_ROOT}/auth/
COPY src/memory/ ${LAMBDA_TASK_ROOT}/memory/
COPY src/utils/ ${LAMBDA_TASK_ROOT}/utils/

# App configuration files (read at runtime by utils.config.read_config).
COPY config/ ${LAMBDA_TASK_ROOT}/config/

CMD [ "history_api_lambda.main.lambda_handler" ]
