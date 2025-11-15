"""Logging utility module for the application.

This module provides a centralized logging utility that creates and manages
a single logger instance for the application. The logger can be imported
and used from anywhere in the codebase.
"""

import logging
import os
import sys
from io import StringIO
from typing import Optional
from urllib.parse import urlparse

try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError
    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False


# Module-level variable to store the logger instance
_app_logger: Optional[logging.Logger] = None


class S3LogHandler(logging.Handler):
    """Custom logging handler that writes logs to S3.
    
    This handler buffers log entries and writes them to S3 when the buffer
    reaches a certain size or when explicitly flushed.
    """
    
    def __init__(self, s3_path: str, buffer_size: int = 1000):
        """Initialize the S3 log handler.
        
        Args:
            s3_path: S3 path in format 's3://bucket-name/path/to/file.log'
            buffer_size: Number of log entries to buffer before writing to S3
        """
        super().__init__()
        if not BOTO3_AVAILABLE:
            raise ImportError(
                "boto3 is required for S3 logging. Install it with: pip install boto3"
            )
        
        # Parse S3 path
        parsed = urlparse(s3_path)
        if parsed.scheme != 's3':
            raise ValueError(f"Invalid S3 path format: {s3_path}. Expected 's3://bucket/path'")
        
        self.bucket_name = parsed.netloc
        self.key = parsed.path.lstrip('/')
        self.buffer_size = buffer_size
        self.buffer = StringIO()
        self.buffer_count = 0
        self.s3_client = boto3.client('s3')
    
    def emit(self, record: logging.LogRecord) -> None:
        """Emit a log record to the buffer."""
        try:
            msg = self.format(record)
            self.buffer.write(msg + '\n')
            self.buffer_count += 1
            
            # Write to S3 if buffer is full
            if self.buffer_count >= self.buffer_size:
                self.flush()
        except Exception:
            self.handleError(record)
    
    def flush(self) -> None:
        """Flush the buffer to S3."""
        if self.buffer_count == 0:
            return
        
        try:
            buffer_content = self.buffer.getvalue()
            if buffer_content:
                # Append to existing file in S3 if it exists
                try:
                    existing_content = self.s3_client.get_object(
                        Bucket=self.bucket_name,
                        Key=self.key
                    )['Body'].read().decode('utf-8')
                    buffer_content = existing_content + buffer_content
                except ClientError as e:
                    if e.response['Error']['Code'] != 'NoSuchKey':
                        raise
                    # File doesn't exist yet, which is fine - we'll create it
                
                # Upload to S3
                self.s3_client.put_object(
                    Bucket=self.bucket_name,
                    Key=self.key,
                    Body=buffer_content.encode('utf-8')
                )
                
                # Clear buffer
                self.buffer = StringIO()
                self.buffer_count = 0
        except (NoCredentialsError, ClientError) as e:
            # Log error but don't raise to avoid breaking the application
            sys.stderr.write(f"Failed to write logs to S3: {e}\n")
        except Exception as e:
            sys.stderr.write(f"Unexpected error writing logs to S3: {e}\n")
    
    def close(self) -> None:
        """Close the handler and flush any remaining logs."""
        self.flush()
        super().close()


def get_logger(name: Optional[str] = None, log_file_path: Optional[str] = None) -> logging.Logger:
    """Get or create the application logger.
    
    This function implements a singleton pattern to ensure only one logger
    instance exists for the application. If a logger has already been created,
    it returns the existing instance. Otherwise, it creates a new logger
    with standard configuration.
    
    Args:
        name: Optional name for the logger. If not provided, defaults to 'app'.
              If a logger already exists, this parameter is ignored.
        log_file_path: Optional path to a log file. Can be:
                      - A local file path (e.g., 'logs/app.log')
                      - An S3 path (e.g., 's3://bucket-name/path/to/file.log')
                      If a logger already exists, this parameter is ignored.
    
    Returns:
        logging.Logger: The application logger instance.
    
    Raises:
        ImportError: If log_file_path is an S3 path but boto3 is not available.
        ValueError: If log_file_path is an invalid S3 path format.
        OSError: If log_file_path is a local path and the directory cannot be created.
    """
    global _app_logger
    
    # Return existing logger if it has already been created
    if _app_logger is not None:
        return _app_logger
    
    # Create new logger
    logger_name = name if name is not None else 'app'
    _app_logger = logging.getLogger(logger_name)
    
    # Only configure if logger doesn't have handlers (avoid duplicate handlers)
    if not _app_logger.handlers:
        _app_logger.setLevel(logging.INFO)
        
        # Create console handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        
        # Create formatter
        formatter = logging.Formatter(
            '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        console_handler.setFormatter(formatter)
        
        # Add handler to logger
        _app_logger.addHandler(console_handler)
        
        # Add file handler if log_file_path is provided
        if log_file_path:
            file_handler = None
            
            # Check if it's an S3 path
            if log_file_path.startswith('s3://'):
                # Create S3 handler
                file_handler = S3LogHandler(log_file_path)
                file_handler.setLevel(logging.INFO)
                file_handler.setFormatter(formatter)
            else:
                # Create local file handler
                # Ensure directory exists
                log_dir = os.path.dirname(log_file_path)
                if log_dir and not os.path.exists(log_dir):
                    os.makedirs(log_dir, exist_ok=True)
                
                file_handler = logging.FileHandler(log_file_path)
                file_handler.setLevel(logging.INFO)
                file_handler.setFormatter(formatter)
            
            if file_handler:
                _app_logger.addHandler(file_handler)
        
        # Prevent propagation to root logger to avoid duplicate logs
        _app_logger.propagate = False
    
    return _app_logger

