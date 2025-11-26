"""AWS utility functions for the application."""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from urllib.parse import urlparse

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


def upload_to_s3(s3_uri: str, local_path: Union[str, Path], aws_profile: Optional[str] = None) -> None:
    """
    Upload a file or folder to S3.
    
    Args:
        s3_uri: S3 URI in format 's3://bucket-name/path/to/destination'
                If the URI points to a directory (ends with '/'), files will be
                uploaded maintaining their relative structure.
                If the URI points to a file, the local file will be uploaded to that exact key.
        local_path: Local file or folder path to upload.
                    If a folder, all files will be uploaded recursively.
        aws_profile: Optional AWS profile name to use for authentication.
                     If not provided, uses the default AWS credentials.
    
    Raises:
        ValueError: If s3_uri is not a valid S3 URI format
        FileNotFoundError: If local_path does not exist
    """
    # Parse S3 URI
    parsed = urlparse(s3_uri)
    if parsed.scheme != 's3':
        raise ValueError(f"Invalid S3 URI format: {s3_uri}. Expected 's3://bucket-name/path'")
    
    bucket = parsed.netloc
    s3_prefix = parsed.path.lstrip('/')
    
    # Normalize local path
    local_path = Path(local_path)
    if not local_path.exists():
        raise FileNotFoundError(f"Local path does not exist: {local_path}")
    
    # Create S3 client with optional profile
    if aws_profile:
        session = boto3.Session(profile_name=aws_profile)
        s3 = session.client("s3")
    else:
        s3 = boto3.client("s3")
    
    # Collect files to upload
    files_to_upload = []
    if local_path.is_file():
        files_to_upload = [local_path]
    elif local_path.is_dir():
        files_to_upload = list(local_path.rglob('*'))
        files_to_upload = [f for f in files_to_upload if f.is_file()]
    else:
        raise ValueError(f"Local path must be a file or directory: {local_path}")
    
    if not files_to_upload:
        log.warning(f"No files found to upload from {local_path}")
        return
    
    # Determine if S3 URI is a directory (ends with '/')
    is_s3_directory = s3_uri.endswith('/')
    
    # Upload files
    for file_path in files_to_upload:
        if local_path.is_file():
            # Single file upload
            if is_s3_directory:
                # If S3 URI is a directory, append the filename
                key = f"{s3_prefix}{file_path.name}"
            else:
                # If S3 URI is a file path, use it as-is
                key = s3_prefix
        else:
            # Directory upload: maintain relative structure
            rel_path = file_path.relative_to(local_path)
            # Ensure prefix ends with '/' for directory uploads
            prefix = s3_prefix if s3_prefix.endswith('/') else f"{s3_prefix}/"
            key = f"{prefix}{rel_path.as_posix()}"
        
        try:
            s3.upload_file(str(file_path), bucket, key)
            log.info(f"Uploaded {file_path} to s3://{bucket}/{key}")
        except ClientError as e:
            log.error(f"Failed to upload {file_path} to s3://{bucket}/{key}: {e}")
            raise
