import logging
from datetime import datetime, timezone

import boto3
from botocore.config import Config

from config.settings import settings

logger = logging.getLogger(__name__)

_s3_client = None


def _get_s3():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client(
            "s3",
            endpoint_url=f"https://{settings.r2_account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=settings.r2_access_key_id,
            aws_secret_access_key=settings.r2_secret_access_key,
            config=Config(signature_version="s3v4"),
            region_name="auto",
        )
    return _s3_client


def upload_file(
    patient_id: str,
    document_type: str,
    filename: str,
    file_bytes: bytes,
    content_type: str,
) -> str:
    """
    Upload a file to Cloudflare R2 and return the storage_path.
    Path format: {patient_id}/{document_type}/{iso_timestamp}_{filename}
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    safe_filename = filename.replace(" ", "_")
    storage_path = f"{patient_id}/{document_type}/{ts}_{safe_filename}"

    s3 = _get_s3()
    s3.put_object(
        Bucket=settings.r2_bucket_name,
        Key=storage_path,
        Body=file_bytes,
        ContentType=content_type,
    )
    logger.info("Uploaded to R2: %s (%d bytes)", storage_path, len(file_bytes))
    return storage_path


def generate_signed_url(storage_path: str, expires_in: int = 900) -> str:
    """
    Generate a pre-signed GET URL for a file in R2.
    Default expiry: 15 minutes (900 seconds).
    IMPORTANT: Only call this after verifying the requesting user owns the patient.
    """
    s3 = _get_s3()
    url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.r2_bucket_name, "Key": storage_path},
        ExpiresIn=expires_in,
    )
    logger.info("Generated signed URL for %s (expires in %ds)", storage_path, expires_in)
    return url
