"""AWS utility functions for the application."""

import json
from typing import Any, Dict

import boto3
from botocore.exceptions import ClientError

from .logger import get_logger

log = get_logger(__name__)


def get_db_credentials_from_secret(secret_name: str, region: str = "us-east-1") -> Dict[str, Any]:
    """
    Retrieve database credentials from AWS Secrets Manager.
    
    Args:
        secret_name: Name of the secret in AWS Secrets Manager
        region: AWS region where the secret is stored (default: us-east-1)
    
    Returns:
        Dictionary with database credentials in format expected by psycopg:
        {'host': ..., 'port': ..., 'dbname': ..., 'user': ..., 'password': ...}
    
    Raises:
        ClientError: If there's an error retrieving the secret
        ValueError: If the secret doesn't contain required fields
    """
    secrets_client = boto3.client('secretsmanager', region_name=region)
    
    try:
        response = secrets_client.get_secret_value(SecretId=secret_name)
        secret_string = response['SecretString']
        secret_dict = json.loads(secret_string)
        
        # Map secret format to psycopg format
        # psycopg3 uses 'dbname' (not 'database') and 'user' (not 'username')
        db_creds = {
            'host': secret_dict.get('host'),
            'port': secret_dict.get('port'),
            'dbname': secret_dict.get('dbname'),
            'user': secret_dict.get('username'),    # Map username to user
            'password': secret_dict.get('password'),
        }
        
        # Validate required fields
        required_fields = ['host', 'port', 'dbname', 'user', 'password']
        missing_fields = [field for field in required_fields if db_creds.get(field) is None]
        if missing_fields:
            raise ValueError(
                f"Secret '{secret_name}' is missing required fields: {', '.join(missing_fields)}"
            )
        
        return db_creds
        
    except ClientError as e:
        log.error(f"Error retrieving secret '{secret_name}': {e}")
        raise
    except json.JSONDecodeError as e:
        log.error(f"Error parsing secret JSON for '{secret_name}': {e}")
        raise ValueError(f"Secret '{secret_name}' does not contain valid JSON") from e

