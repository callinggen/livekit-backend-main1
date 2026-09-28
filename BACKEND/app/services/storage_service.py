import os
from pathlib import Path
from typing import Optional


class StorageService:
    """
    Decoupled Cloud & Local Media Storage Service.
    Enables zero-disk-pressure architecture by streaming and storing call recordings
    directly to AWS S3 (when configured) while providing transparent local fallback.
    """

    _s3_client = None
    _s3_bucket = os.getenv("S3_RECORDINGS_BUCKET") or os.getenv("S3_BUCKET_NAME") or os.getenv("AWS_STORAGE_BUCKET_NAME")
    _aws_region = os.getenv("AWS_REGION", "us-east-1")

    @classmethod
    def get_s3_client(cls):
        if cls._s3_client is None and cls._s3_bucket:
            try:
                import importlib
                boto3 = importlib.import_module("boto3")
                cls._s3_client = boto3.client(
                    "s3",
                    region_name=cls._aws_region,
                    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
                    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
                )
                print(f"[StorageService] AWS S3 client initialized for bucket: {cls._s3_bucket}")
            except Exception as e:
                print(f"[StorageService] Could not initialize S3 client: {e}")
                cls._s3_client = None
        return cls._s3_client

    @classmethod
    def is_s3_enabled(cls) -> bool:
        return bool(cls._s3_bucket and cls.get_s3_client())

    @classmethod
    def upload_recording(cls, local_path: str, filename: Optional[str] = None) -> Optional[str]:
        """
        Uploads a local recording file to S3.
        Returns the S3 key if successful, or None.
        """
        if not cls.is_s3_enabled():
            return None

        client = cls.get_s3_client()
        if not client:
            return None

        s3_key = filename or Path(local_path).name
        try:
            client.upload_file(
                Filename=local_path,
                Bucket=cls._s3_bucket,
                Key=s3_key,
                ExtraArgs={"ContentType": "audio/wav"},
            )
            print(f"[StorageService] Uploaded {s3_key} to s3://{cls._s3_bucket}/{s3_key}")
            return s3_key
        except Exception as e:
            print(f"[StorageService] Failed to upload {s3_key} to S3: {e}")
            return None

    @classmethod
    def get_presigned_url(cls, filename: str, expiration_seconds: int = 900) -> Optional[str]:
        """
        Generates an ephemeral presigned GET URL for an S3 recording (default 15 minutes).
        Returns None if S3 is not configured or object does not exist.
        """
        if not cls.is_s3_enabled():
            return None

        client = cls.get_s3_client()
        if not client:
            return None

        try:
            # Check if key exists in S3
            client.head_object(Bucket=cls._s3_bucket, Key=filename)
            url = client.generate_presigned_url(
                ClientMethod="get_object",
                Params={"Bucket": cls._s3_bucket, "Key": filename},
                ExpiresIn=expiration_seconds,
            )
            return url
        except Exception:
            return None

    @classmethod
    def get_local_path(cls, filename: str) -> Optional[str]:
        """Returns local path if file exists on disk, else None."""
        base_dir = Path("recordings")
        candidate = base_dir / filename
        if candidate.is_file():
            return str(candidate)
        return None
