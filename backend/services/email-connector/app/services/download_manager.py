import hashlib
import logging

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import Settings

logger = logging.getLogger(__name__)


class DownloadManager:
    """
    Stores attachment bytes in S3-compatible storage and computes SHA-256 for
    dedup. Unlike slack-connector's DownloadManager, this never fetches bytes
    itself — Gmail's attachments.get already returns the content inline
    (base64), so GmailClient.get_attachment_bytes has done that part before
    this is called.
    """

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
        """Uploads already-fetched bytes to S3. Returns (s3_key, sha256_hash).

        `key_id` must be a short, internally-owned identifier (the
        EmailAttachment row's own UUID) — never a provider-supplied token.
        Gmail's attachmentId regularly runs 300-400+ characters, and MinIO
        (backed by a normal filesystem) rejects any single "/"-separated S3
        key segment over ~255 bytes as XMinioInvalidObjectName. Found live:
        every attachment in the first real sync failed to store until the
        call site stopped passing Gmail's id here.
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
            logger.error("S3 upload error for %s: %s", attachment_id, e)
            raise

    async def verify_s3_file(self, s3_key: str, expected_sha256: str) -> bool:
        try:
            response = self.s3_client.get_object(Bucket=self.bucket_name, Key=s3_key)
            content = response["Body"].read()
            return hashlib.sha256(content).hexdigest() == expected_sha256
        except (ClientError, BotoCoreError) as e:
            logger.warning("Failed to verify S3 file %s: %s", s3_key, e)
            return False

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
