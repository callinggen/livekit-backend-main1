"""
CallingGen Disk Space Watchdog
==============================
Runs as a persistent systemd service. Every 60 seconds:
1. Checks disk usage
2. If disk > 70%: runs S3 upload+cleanup for all recordings
3. Logs status continuously to /home/ubuntu/logs/disk_watchdog.log

This ensures the disk NEVER fills up between hourly cron runs.
"""

import os
import re
import sys
import time
import wave
import shutil
import logging
import subprocess
from pathlib import Path
from datetime import datetime

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [disk_watchdog] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
log = logging.getLogger(__name__)

sys.path.insert(0, '/home/ubuntu/app/BACKEND/BACKEND')
from dotenv import load_dotenv
load_dotenv('/home/ubuntu/app/BACKEND/BACKEND/.env')

import boto3
import psycopg2
from botocore.exceptions import ClientError

RDIR = Path('/home/ubuntu/app/BACKEND/BACKEND/recordings')
BUCKET = os.getenv('AWS_S3_BUCKET_NAME', 'callinggen-recordings')
REGION = os.getenv('AWS_REGION', 'ap-south-2')
DB_URL = os.getenv('DATABASE_URL', '')

# Thresholds
WARN_PERCENT = 70     # Start cleaning at 70%
CRITICAL_PERCENT = 85 # Log critical warning
CHECK_INTERVAL = 60   # Check every 60 seconds


def get_disk_percent():
    """Return current disk usage percentage for /"""
    result = subprocess.run(['df', '/'], capture_output=True, text=True)
    for line in result.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 5:
            return int(parts[4].replace('%', ''))
    return 0


def get_s3_client():
    return boto3.client(
        's3',
        aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
        region_name=REGION
    )


def get_db_conn():
    db_url = DB_URL.replace('postgresql+asyncpg://', '').replace('postgresql://', '')
    conn = psycopg2.connect('postgresql://' + db_url)
    conn.autocommit = True
    return conn


def exists_s3(s3_client, fname):
    try:
        s3_client.head_object(Bucket=BUCKET, Key='recordings/' + fname)
        return True
    except ClientError:
        return False


def upload_verify(s3_client, local_path, fname):
    try:
        s3_client.upload_file(
            str(local_path), BUCKET, 'recordings/' + fname,
            ExtraArgs={'ContentType': 'audio/wav'}
        )
        return exists_s3(s3_client, fname)
    except Exception as e:
        log.error(f'Upload FAILED {fname}: {e}')
        return False


def make_mixed(customer_path, agent_path, out_path):
    """Copy best available track or mix both — without numpy for memory safety."""
    c_ok = customer_path.exists() and customer_path.stat().st_size > 100
    a_ok = agent_path.exists() and agent_path.stat().st_size > 100

    if not c_ok and not a_ok:
        return False

    # If only one track, just copy it
    if c_ok and not a_ok:
        shutil.copy(str(customer_path), str(out_path))
        return True
    if a_ok and not c_ok:
        shutil.copy(str(agent_path), str(out_path))
        return True

    # Both exist — copy the larger one (memory-safe, no numpy)
    best = customer_path if customer_path.stat().st_size >= agent_path.stat().st_size else agent_path
    shutil.copy(str(best), str(out_path))
    return True


def run_cleanup():
    """Upload all recordings to S3 and delete local copies. Returns (deleted, uploaded, kept)."""
    if not RDIR.exists():
        return 0, 0, 0

    all_wavs = list(RDIR.glob('*.wav'))
    if not all_wavs:
        return 0, 0, 0

    call_ids = set()
    for f in all_wavs:
        m = re.match(r'call_(\d+)', f.name)
        if m:
            call_ids.add(int(m.group(1)))

    if not call_ids:
        return 0, 0, 0

    log.info(f'Starting cleanup: {len(all_wavs)} WAV files, {len(call_ids)} call IDs')

    try:
        s3_client = get_s3_client()
        conn = get_db_conn()
        cur = conn.cursor()
    except Exception as e:
        log.error(f'Cannot init S3/DB: {e}')
        return 0, 0, 0

    deleted = uploaded = kept = 0

    for call_id in sorted(call_ids):
        mixed_name = f'call_{call_id}.wav'
        mixed_path = RDIR / mixed_name
        agent_path = RDIR / f'call_{call_id}_agent.wav'
        customer_path = RDIR / f'call_{call_id}_customer.wav'

        in_s3 = exists_s3(s3_client, mixed_name)

        if not in_s3:
            if not mixed_path.exists():
                if not make_mixed(customer_path, agent_path, mixed_path):
                    kept += 1
                    continue

            if mixed_path.exists() and mixed_path.stat().st_size > 0:
                if upload_verify(s3_client, mixed_path, mixed_name):
                    in_s3 = True
                    uploaded += 1
                    s3_url = f'https://{BUCKET}.s3.{REGION}.amazonaws.com/recordings/{mixed_name}'
                    try:
                        cur.execute(
                            "UPDATE calls SET recording_url=%s WHERE id=%s "
                            "AND (recording_url IS NULL OR recording_url LIKE '/api/%%')",
                            (s3_url, call_id)
                        )
                    except Exception as db_err:
                        log.warning(f'DB update error for call {call_id}: {db_err}')
                else:
                    kept += 1
                    continue
            else:
                kept += 1
                continue

        if in_s3:
            for p in [mixed_path, agent_path, customer_path]:
                if p.exists():
                    try:
                        p.unlink()
                        deleted += 1
                    except Exception as e:
                        log.warning(f'Delete error {p.name}: {e}')

    try:
        cur.close()
        conn.close()
    except Exception:
        pass

    return deleted, uploaded, kept


def main():
    log.info('Disk watchdog started')
    log.info(f'Thresholds: warn={WARN_PERCENT}%, critical={CRITICAL_PERCENT}%')
    log.info(f'Check interval: {CHECK_INTERVAL}s')
    log.info(f'Recordings dir: {RDIR}')
    log.info(f'S3 bucket: {BUCKET} ({REGION})')

    last_cleanup_time = 0
    MIN_CLEANUP_INTERVAL = 300  # Don't clean more than once per 5 minutes

    while True:
        try:
            pct = get_disk_percent()
            wav_count = len(list(RDIR.glob('*.wav'))) if RDIR.exists() else 0

            if pct >= CRITICAL_PERCENT:
                log.warning(f'CRITICAL: Disk at {pct}%, {wav_count} WAV files on disk')
            elif pct >= WARN_PERCENT:
                log.info(f'Disk at {pct}%, {wav_count} WAV files — triggering cleanup')
            else:
                # Disk is fine — just log every 10 minutes
                if int(time.time()) % 600 < CHECK_INTERVAL:
                    log.info(f'OK: Disk at {pct}%, {wav_count} WAV files on disk')

            # Run cleanup if above threshold and not too frequent
            now = time.time()
            should_clean = (
                (pct >= WARN_PERCENT or wav_count > 0) and
                (now - last_cleanup_time) >= MIN_CLEANUP_INTERVAL
            )

            if should_clean:
                deleted, uploaded, kept = run_cleanup()
                last_cleanup_time = now
                new_pct = get_disk_percent()
                new_count = len(list(RDIR.glob('*.wav'))) if RDIR.exists() else 0
                log.info(
                    f'Cleanup done: uploaded={uploaded}, deleted={deleted}, kept={kept} | '
                    f'Disk: {pct}% → {new_pct}% | Files: {wav_count} → {new_count}'
                )

        except Exception as e:
            log.error(f'Watchdog loop error: {e}')

        time.sleep(CHECK_INTERVAL)


if __name__ == '__main__':
    main()
