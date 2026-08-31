import hashlib
import logging

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import Settings

logger = logging.getLogger(__name__)


class StorageManager:
    """Stores browser-uploaded documents in S3-compatible storage and
    computes their SHA-256. Same shape as Invoice Service's own
    StorageManager and the connectors' DownloadManager — deliberately
    identical, so storage behaves the same wherever a file enters."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.s3_client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
            region_name="us-east-1",
        )
        self.preview_s3_client = boto3.client(
            "s3",
            endpoint_url=settings.s3_public_endpoint_url or settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
            region_name="us-east-1",
        )
        self.bucket_name = settings.s3_bucket_name

    async def store_bytes(self, key_id: str, filename: str, content: bytes) -> tuple[str, str]:
        """Uploads bytes to S3. Returns (s3_key, sha256_hash).

        `key_id` must be a short, internally-owned identifier (the
        Document row's own UUID), never anything derived from the upload
        itself. The Email connector learned this the hard way: keying on a
        provider-supplied id produced keys MinIO rejected outright
        (`XMinioInvalidObjectName`) once they exceeded ~255 bytes.
        """
        try:
            sha256_hash = hashlib.sha256(content).hexdigest()
            s3_key = f"{key_id}/{filename}"

            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=s3_key,
                Body=content,
                ContentType="application/octet-stream",
                Metadata={"original_filename": filename},
            )

            logger.info("Successfully stored %s to %s", filename, s3_key)
            return s3_key, sha256_hash

        except (ClientError, BotoCoreError) as e:
            logger.error("S3 upload error for %s: %s", key_id, e)
            raise

    def get_download_url(
        self, s3_key: str, expiration: int = 3600, mimetype: str | None = None, filename: str | None = None,
    ) -> str:
        params = {"Bucket": self.bucket_name, "Key": s3_key}
        if mimetype:
            params["ResponseContentType"] = mimetype
        if filename:
            safe_filename = filename.replace('"', "'").replace("\r", "").replace("\n", "")
            params["ResponseContentDisposition"] = f'inline; filename="{safe_filename}"'
        return self.preview_s3_client.generate_presigned_url("get_object", Params=params, ExpiresIn=expiration)
