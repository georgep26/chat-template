FROM public.ecr.aws/lambda/python:3.11

# Install libpq for psycopg2 if needed
RUN yum -y install postgresql15-libs && yum clean all

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install -r requirements.txt --no-cache-dir

# Copy application source code
COPY src/ ${LAMBDA_TASK_ROOT}/
COPY config/ ${LAMBDA_TASK_ROOT}/../config/

# Set the Lambda handler
CMD [ "chat_app_lambda.lambda_handler" ]

