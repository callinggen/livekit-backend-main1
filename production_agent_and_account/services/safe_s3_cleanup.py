import os, re, sys, wave, shutil, boto3
from botocore.exceptions import ClientError
from pathlib import Path
import psycopg2

sys.path.insert(0, '/home/ubuntu/app/BACKEND/BACKEND')
from dotenv import load_dotenv
load_dotenv('/home/ubuntu/app/BACKEND/BACKEND/.env')

RDIR = Path('/home/ubuntu/app/BACKEND/BACKEND/recordings')
BUCKET = os.getenv('AWS_S3_BUCKET_NAME', 'callinggen-recordings')
REGION = os.getenv('AWS_REGION', 'ap-south-2')
DB_URL = os.getenv('DATABASE_URL', '')

s3 = boto3.client('s3',
    aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
    aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
    region_name=REGION)

db_url = DB_URL.replace('postgresql+asyncpg://', '').replace('postgresql://', '')
conn = psycopg2.connect('postgresql://' + db_url)
conn.autocommit = True
cur = conn.cursor()


def exists_s3(fname):
    try:
        s3.head_object(Bucket=BUCKET, Key='recordings/' + fname)
        return True
    except ClientError:
        return False


def upload_verify(local_path, fname):
    try:
        s3.upload_file(str(local_path), BUCKET, 'recordings/' + fname,
                       ExtraArgs={'ContentType': 'audio/wav'})
        ok = exists_s3(fname)
        if ok:
            print(f'[cron_cleanup] Uploaded: {fname}')
        return ok
    except Exception as e:
        print(f'[cron_cleanup] Upload FAILED {fname}: {e}')
        return False


def best_available_track(customer_path, agent_path):
    """Return the largest/best single track to use as fallback recording."""
    c_ok = customer_path.exists() and customer_path.stat().st_size > 100
    a_ok = agent_path.exists() and agent_path.stat().st_size > 100
    if c_ok and a_ok:
        return customer_path if customer_path.stat().st_size >= agent_path.stat().st_size else agent_path
    if c_ok:
        return customer_path
    if a_ok:
        return agent_path
    return None


def make_mixed(customer_path, agent_path, out_path):
    """Mix two tracks; fallback to copying the best single track."""
    c_ok = customer_path.exists() and customer_path.stat().st_size > 100
    a_ok = agent_path.exists() and agent_path.stat().st_size > 100

    if not c_ok and not a_ok:
        return False

    if c_ok and a_ok:
        try:
            import numpy as np
            tracks = [wave.open(str(p), 'rb') for p in [customer_path, agent_path]]
            params = tracks[0].getparams()
            datas = [t.readframes(t.getnframes()) for t in tracks]
            for t in tracks:
                t.close()
            arrays = [np.frombuffer(d, dtype='int16').astype('int32') for d in datas]
            maxl = max(len(a) for a in arrays)
            padded = [np.pad(a, (0, maxl - len(a))) for a in arrays]
            mixed = np.clip(sum(padded), -32768, 32767).astype('int16')
            with wave.open(str(out_path), 'wb') as w:
                w.setparams(params)
                w.writeframes(mixed.tobytes())
            return True
        except Exception:
            pass  # fall through to copy fallback

    # Fallback: just copy the best available track
    best = best_available_track(customer_path, agent_path)
    if best:
        shutil.copy(str(best), str(out_path))
        return True
    return False


if not RDIR.exists():
    print('[cron_cleanup] No recordings directory. Done.')
    sys.exit(0)

all_wavs = list(RDIR.glob('*.wav'))
if not all_wavs:
    print('[cron_cleanup] No WAV files. Done.')
    sys.exit(0)

print(f'[cron_cleanup] Found {len(all_wavs)} WAV files. Processing...')

call_ids = set()
for f in all_wavs:
    m = re.match(r'call_(\d+)', f.name)
    if m:
        call_ids.add(int(m.group(1)))

deleted = uploaded = skipped = 0

for call_id in sorted(call_ids):
    mixed_name = f'call_{call_id}.wav'
    mixed_path = RDIR / mixed_name
    agent_path = RDIR / f'call_{call_id}_agent.wav'
    customer_path = RDIR / f'call_{call_id}_customer.wav'

    # Step 1: ensure the mixed file is in S3
    in_s3 = exists_s3(mixed_name)

    if not in_s3:
        # Create mixed file if it doesn't exist yet
        if not mixed_path.exists():
            if not make_mixed(customer_path, agent_path, mixed_path):
                print(f'[cron_cleanup] Cannot build recording for call {call_id} — skipping')
                skipped += 1
                continue

        # Upload and verify
        if mixed_path.exists() and mixed_path.stat().st_size > 0:
            if upload_verify(mixed_path, mixed_name):
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
                    print(f'[cron_cleanup] DB update error for call {call_id}: {db_err}')
            else:
                print(f'[cron_cleanup] Upload failed for call {call_id} — keeping local files')
                skipped += 1
                continue
        else:
            skipped += 1
            continue

    # Step 2: ONLY delete local files after S3 is confirmed
    if in_s3:
        for p in [mixed_path, agent_path, customer_path]:
            if p.exists():
                try:
                    p.unlink()
                    deleted += 1
                except Exception as e:
                    print(f'[cron_cleanup] Delete error {p.name}: {e}')

cur.close()
conn.close()

import subprocess
r = subprocess.run(['df', '-h', '/'], capture_output=True, text=True)
remaining = list(RDIR.glob('*.wav'))
print(f'[cron_cleanup] Done. Deleted={deleted}, Uploaded={uploaded}, Kept={skipped}')
print(f'[cron_cleanup] Remaining files: {len(remaining)}')
print(r.stdout.strip())
