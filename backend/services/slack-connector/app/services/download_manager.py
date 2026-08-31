import hashlib
import logging
from typing import Optional

import boto3
import httpx
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import Settings

logger = logging.getLogger(__name__)


class DownloadManager:
    """
    Manages file downloads from Slack to S3-compatible storage.
    Computes SHA-256 for deduplication.
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

    async def download_and_store(
        self,
        file_id: str,
        filename: str,
        download_url: str,
        bot_token: str,
    ) -> tuple[str, str]:
        """
        Download a file from Slack and store it in S3.

        Args:
            file_id: Slack file ID
            filename: Original filename
            download_url: Slack private download URL
            bot_token: Bot token for authentication

        Returns:
            tuple: (s3_key, sha256_hash)

        Raises:
            Exception: If download or upload fails
        """
        try:
            # Download from Slack
            content, sha256_hash = await self._download_from_slack(download_url, bot_token)

            # Upload to S3
            s3_key = await self._upload_to_s3(file_id, filename, content)

            logger.info(f"Successfully downloaded and stored {filename} to {s3_key}")
            return s3_key, sha256_hash

        except Exception as e:
            logger.error(f"Failed to download/store file {file_id} ({filename}): {e}")
            raise

    async def _download_from_slack(self, download_url: str, bot_token: str) -> tuple[bytes, str]:
        """Download file content from Slack and compute SHA-256."""
        try:
            headers = {"Authorization": f"Bearer {bot_token}"}

            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.get(download_url, headers=headers)
                response.raise_for_status()

            content = response.content

            # Compute SHA-256
            sha256_hash = hashlib.sha256(content).hexdigest()

            logger.debug(f"Downloaded {len(content)} bytes, SHA-256: {sha256_hash}")
            return content, sha256_hash

        except httpx.HTTPError as e:
            logger.error(f"HTTP error downloading from {download_url}: {e}")
            raise
        except Exception as e:
            logger.error(f"Error downloading from {download_url}: {e}")
            raise

    async def _upload_to_s3(self, file_id: str, filename: str, content: bytes) -> str:
        """Upload file content to S3."""
        try:
            # Generate S3 key: {file_id}/{filename}
            s3_key = f"{file_id}/{filename}"

            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=s3_key,
                Body=content,
                ContentType="application/octet-stream",
                Metadata={
                    "original_filename": filename,
                    "slack_file_id": file_id,
                },
            )

            logger.debug(f"Uploaded to S3: {s3_key}")
            return s3_key

        except (ClientError, BotoCoreError) as e:
            logger.error(f"S3 upload error for {file_id}: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error uploading {file_id} to S3: {e}")
            raise

    async def verify_s3_file(self, s3_key: str, expected_sha256: str) -> bool:
        """Verify that a file was stored correctly in S3."""
        try:
            response = self.s3_client.get_object(Bucket=self.bucket_name, Key=s3_key)
            content = response["Body"].read()
            computed_sha256 = hashlib.sha256(content).hexdigest()
            return computed_sha256 == expected_sha256
        except (ClientError, BotoCoreError) as e:
            logger.warning(f"Failed to verify S3 file {s3_key}: {e}")
            return False
        except Exception as e:
            logger.warning(f"Unexpected error verifying S3 file {s3_key}: {e}")
            return False

    def get_download_url(
        self,
        s3_key: str,
        expiration: int = 3600,
        mimetype: str | None = None,
        filename: str | None = None,
    ) -> str:
        """
        Generate a pre-signed URL for downloading from S3.

        Args:
            s3_key: S3 object key
            expiration: URL expiration time in seconds

        Returns:
            Pre-signed URL
        """
        try:
            params = {"Bucket": self.bucket_name, "Key": s3_key}
            if mimetype:
                params["ResponseContentType"] = mimetype
            if filename:
                safe_filename = filename.replace('"', "'").replace("\r", "").replace("\n", "")
                params["ResponseContentDisposition"] = f'inline; filename="{safe_filename}"'

            url = self.preview_s3_client.generate_presigned_url(
                "get_object",
                Params=params,
                ExpiresIn=expiration,
            )
            return url
        except (ClientError, BotoCoreError) as e:
            logger.error(f"Failed to generate presigned URL for {s3_key}: {e}")
            raise
