"""Configuration utility module for the application.

This module provides a function to read configuration files from local filesystem
or S3, supporting both JSON and YAML formats.
"""

import json
import os
from typing import Dict, Any
from urllib.parse import urlparse

try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False

try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError
    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False


def read_config(config_path: str) -> Dict[str, Any]:
    """Read configuration from a local file or S3 path.
    
    This function supports reading configuration files in JSON or YAML format
    from either the local filesystem or S3. The file format is automatically
    detected based on the file extension (.json, .yaml, .yml).
    
    Args:
        config_path: Path to the configuration file. Can be:
                    - A local file path (e.g., 'config/app_config.yml')
                    - An S3 path (e.g., 's3://bucket-name/path/to/config.json')
    
    Returns:
        Dict[str, Any]: The configuration as a dictionary.
    
    Raises:
        ImportError: If config_path is an S3 path but boto3 is not available,
                    or if config_path is a YAML file but yaml module is not available.
        ValueError: If config_path is an invalid S3 path format, or if the file
                   extension is not supported (.json, .yaml, .yml).
        FileNotFoundError: If the local file does not exist.
        json.JSONDecodeError: If the JSON file is malformed.
        yaml.YAMLError: If the YAML file is malformed.
        ClientError: If there's an error accessing the S3 file.
        NoCredentialsError: If AWS credentials are not configured.
    """
    # Determine if it's an S3 path
    is_s3_path = config_path.startswith('s3://')
    
    if is_s3_path:
        if not BOTO3_AVAILABLE:
            raise ImportError(
                "boto3 is required for S3 config files. Install it with: pip install boto3"
            )
        
        # Parse S3 path
        parsed = urlparse(config_path)
        if parsed.scheme != 's3':
            raise ValueError(f"Invalid S3 path format: {config_path}. Expected 's3://bucket/path'")
        
        bucket_name = parsed.netloc
        key = parsed.path.lstrip('/')
        
        if not bucket_name:
            raise ValueError(f"Invalid S3 path: missing bucket name in {config_path}")
        
        if not key:
            raise ValueError(f"Invalid S3 path: missing key/path in {config_path}")
        
        # Download file content from S3
        s3_client = boto3.client('s3')
        try:
            response = s3_client.get_object(Bucket=bucket_name, Key=key)
            content = response['Body'].read().decode('utf-8')
        except NoCredentialsError:
            raise ValueError(
                "AWS credentials not found. Configure credentials using AWS CLI or "
                "environment variables."
            )
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', '')
            if error_code == 'NoSuchKey':
                raise FileNotFoundError(f"Config file not found in S3: {config_path}")
            raise ClientError(
                e.response['Error'],
                e.operation_name
            )
    else:
        # Read from local filesystem
        if not os.path.exists(config_path):
            raise FileNotFoundError(f"Config file not found: {config_path}")
        
        with open(config_path, 'r', encoding='utf-8') as f:
            content = f.read()
    
    # Determine file format based on extension
    if is_s3_path:
        # Extract extension from S3 key
        _, ext = os.path.splitext(key)
    else:
        _, ext = os.path.splitext(config_path)
    
    ext = ext.lower()
    
    # Parse content based on file format
    if ext == '.json':
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            raise json.JSONDecodeError(
                f"Invalid JSON in config file: {config_path}",
                e.doc,
                e.pos
            )
    elif ext in ('.yaml', '.yml'):
        if not YAML_AVAILABLE:
            raise ImportError(
                "yaml module is required for YAML config files. Install it with: pip install pyyaml"
            )
        
        try:
            return yaml.safe_load(content)
        except yaml.YAMLError as e:
            raise yaml.YAMLError(f"Invalid YAML in config file: {config_path}") from e
    else:
        raise ValueError(
            f"Unsupported file format: {ext}. Supported formats: .json, .yaml, .yml"
        )

