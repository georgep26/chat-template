# Dockerfile for AWS Lambda Library API application.
# Build from project root:
#   For x86_64: docker build --platform linux/amd64 -f src/library_api_lambda/Dockerfile -t library-api-lambda .
#   For arm64:  docker build --platform linux/arm64 -f src/library_api_lambda/Dockerfile -t library-api-lambda .
FROM public.ecr.aws/lambda/python:3.11

COPY src/library_api_lambda/requirements.txt ${LAMBDA_TASK_ROOT}/

RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

# Application code (library_api_lambda package).
COPY src/library_api_lambda/ ${LAMBDA_TASK_ROOT}/library_api_lambda/

# Shared modules used across Lambdas.
COPY src/auth/ ${LAMBDA_TASK_ROOT}/auth/
COPY src/utils/ ${LAMBDA_TASK_ROOT}/utils/

# App configuration files (read at runtime by utils.config.read_config).
COPY config/ ${LAMBDA_TASK_ROOT}/config/

CMD [ "library_api_lambda.main.lambda_handler" ]
