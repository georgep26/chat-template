# Dockerfile for AWS Lambda document ingestion application.
# Build from project root:
#   docker build --platform linux/amd64 -f src/document_ingestion_lambda/Dockerfile -t document-ingestion-lambda .
FROM public.ecr.aws/lambda/python:3.11

COPY src/document_ingestion_lambda/requirements.txt ${LAMBDA_TASK_ROOT}/

RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

COPY src/document_ingestion_lambda/ ${LAMBDA_TASK_ROOT}/document_ingestion_lambda/
COPY src/utils/ ${LAMBDA_TASK_ROOT}/utils/

CMD [ "document_ingestion_lambda.main.lambda_handler" ]
