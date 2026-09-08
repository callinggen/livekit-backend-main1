import os
import boto3
from botocore.exceptions import BotoCoreError, ClientError

AWS_S3_BUCKET = os.getenv("AWS_S3_BUCKET_NAME", "callinggen-recordings")
AWS_REGION = os.getenv("AWS_REGION", "ap-south-2")


def get_s3_client():
    access_key = os.getenv("AWS_ACCESS_KEY_ID")
    secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
    if not access_key or not secret_key:
        print("[s3_service] WARNING: AWS credentials missing in environment.")
        return None
    return boto3.client(
        "s3",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=AWS_REGION,
    )


def file_exists_in_s3(filename: str) -> bool:
    """Check if a file already exists in S3 before uploading."""
    s3_client = get_s3_client()
    if s3_client is None:
        return False
    try:
        s3_client.head_object(
            Bucket=AWS_S3_BUCKET,
            Key=f"recordings/{filename}"
        )
        return True
    except ClientError as e:
        if e.response['Error']['Code'] == '404':
            return False
        return False


def upload_to_s3_and_delete_local(local_file_path: str, s3_key: str | None = None) -> str | None:
    """
    Upload a local .wav recording file to AWS S3 bucket,
    VERIFY the upload succeeded (via head_object), THEN delete the local file.
    Returns the S3 object URL or None on failure.
    SAFETY: Never deletes local file if S3 upload cannot be verified.
    """
    if not os.path.exists(local_file_path):
        print(f"[s3_service] File not found: {local_file_path}")
        return None

    filename = os.path.basename(local_file_path)
    if s3_key is None:
        s3_key = f"recordings/{filename}"

    s3_client = get_s3_client()
    if s3_client is None:
        print("[s3_service] Skipping S3 upload (no S3 client).")
        return None

    try:
        file_size_mb = os.path.getsize(local_file_path) / 1024 / 1024
        print(f"[s3_service] Uploading {local_file_path} ({file_size_mb:.1f} MB) to s3://{AWS_S3_BUCKET}/{s3_key}...")

        s3_client.upload_file(
            local_file_path,
            AWS_S3_BUCKET,
            s3_key,
            ExtraArgs={"ContentType": "audio/wav"}
        )

        # CRITICAL: Verify the upload actually succeeded before deleting local file
        try:
            s3_client.head_object(Bucket=AWS_S3_BUCKET, Key=s3_key)
        except ClientError:
            print(f"[s3_service] SAFETY ABORT: Upload appeared to succeed but file not found in S3! Keeping local file: {local_file_path}")
            return None

        s3_url = f"https://{AWS_S3_BUCKET}.s3.{AWS_REGION}.amazonaws.com/{s3_key}"
        print(f"[s3_service] Upload verified! S3 URL: {s3_url}")

        # Safe to delete local file now
        try:
            os.remove(local_file_path)
            print(f"[s3_service] Cleaned up local file: {local_file_path}")
        except Exception as del_err:
            print(f"[s3_service] Warning: Could not delete local file {local_file_path}: {del_err}")

        return s3_url

    except (BotoCoreError, ClientError, Exception) as e:
        print(f"[s3_service] Error uploading {local_file_path} to S3: {e}")
        return None


def cleanup_track_files(call_id: int, recordings_dir: str = "recordings") -> None:
    """
    Safely delete individual track files (agent.wav, customer.wav) for a call
    ONLY if the mixed .wav file already exists in S3.
    Called after successful mix+upload.
    """
    mixed_filename = f"call_{call_id}.wav"
    s3_key_mixed = f"recordings/{mixed_filename}"

    # Verify mixed file is in S3 before cleaning up tracks
    s3_client = get_s3_client()
    if s3_client is None:
        print(f"[s3_service] Cannot verify S3 — keeping local track files for call {call_id}")
        return

    try:
        s3_client.head_object(Bucket=AWS_S3_BUCKET, Key=s3_key_mixed)
    except ClientError:
        print(f"[s3_service] Mixed file not found in S3 — keeping local tracks for call {call_id}")
        return

    # Mixed is in S3 — safe to delete individual tracks
    for suffix in ["_agent.wav", "_customer.wav"]:
        track_path = os.path.join(recordings_dir, f"call_{call_id}{suffix}")
        if os.path.exists(track_path):
            try:
                os.remove(track_path)
                print(f"[s3_service] Deleted local track: {track_path}")
            except Exception as e:
                print(f"[s3_service] Warning: Could not delete track {track_path}: {e}")
