# Dockerfile for AWS Lambda Knowledge API application.
# Build from project root:
#   For x86_64: docker build --platform linux/amd64 -f src/knowledge_api_lambda/Dockerfile -t knowledge-api-lambda .
#   For arm64:  docker build --platform linux/arm64 -f src/knowledge_api_lambda/Dockerfile -t knowledge-api-lambda .
FROM public.ecr.aws/lambda/python:3.11

# Build tools needed when langchain pulls in numpy/pydantic-core wheels that
# don't ship a manylinux wheel for the Lambda runtime.
RUN yum -y install gcc gcc-c++ make && yum clean all

COPY src/knowledge_api_lambda/requirements.txt ${LAMBDA_TASK_ROOT}/

RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

# Application code (knowledge_api_lambda package).
COPY src/knowledge_api_lambda/ ${LAMBDA_TASK_ROOT}/knowledge_api_lambda/

# Shared modules used across Lambdas.
COPY src/auth/ ${LAMBDA_TASK_ROOT}/auth/
COPY src/utils/ ${LAMBDA_TASK_ROOT}/utils/
COPY src/knowledge/ ${LAMBDA_TASK_ROOT}/knowledge/

# App configuration files (read at runtime by utils.config.read_config).
COPY config/ ${LAMBDA_TASK_ROOT}/config/

CMD [ "knowledge_api_lambda.main.lambda_handler" ]
