# Production Agent & Account Isolated Backup

This directory was created as an isolated, self-contained reference and backup of the **LiveKit Voice Agent** and all configurations, credentials, and scripts specific to the **Morning Tax production account**.

> [!IMPORTANT]
> **Safety Notice:**
> - No files or settings were modified on the production server (`3.85.244.64`).
> - No existing code files in `BACKEND` or `FRONTEND` were altered.
> - Nothing has been committed or pushed to remote git repositories.
> - This folder is located entirely locally in the root directory: `production_agent_and_account/`.

---

## Directory Structure

```
production_agent_and_account/
├── .env                              # Copy of complete production environment variables
├── README.md                         # This reference documentation
├── account/
│   ├── account_profile.json          # Morning Tax profile (User ID 1, credit balance, caller ID, models)
│   ├── morning_tax_prompts.py        # Complete bot personas (Meera, Raj, John, Voice-E), scripts, qualification
│   └── telnyx_sip_config.json        # Telnyx SIP trunk config (ST_AB44u8gKNDkE, user livekitny, caller ID)
├── agent/
│   ├── agent.py                      # Production voice agent worker (Sarvam bulbul:v3, DeepSeek, audio mixing)
│   ├── backend_client.py             # Internal API notifier client for call status
│   ├── conversation_state.py         # In-memory active call state store
│   ├── finish_call.py                # Call termination, transcript generator, S3 audio upload & room deletion
│   ├── requirements_agent.txt        # Python dependencies specifically required for the agent
│   └── s3_service.py                 # AWS S3 recording uploader with verification and safe local file cleanup
├── config/
│   ├── .env.production               # Production environment variables file (.env)
│   ├── livekit.yaml                  # Production LiveKit Server configuration (/etc/livekit.yaml)
│   └── sip.yaml                      # Production LiveKit SIP Gateway configuration (/etc/sip.yaml)
├── keys/
│   ├── callinggen-key-prod.pem       # Production EC2 SSH private key
│   ├── ssh_connect.bat               # Windows batch script to SSH directly to production EC2
│   └── ssh_connect.sh                # Linux/macOS bash script to SSH directly to production EC2
└── services/
    ├── callinggen-disk-watchdog.service # Systemd watchdog service protecting server disk space
    ├── disk_watchdog.py              # Automated disk monitor and trigger script
    └── safe_s3_cleanup.py            # Hourly cron job verifying S3 recordings and freeing disk space
```

---

## Production Credentials Quick Reference

| Service | Setting / Credential |
| :--- | :--- |
| **Production Server** | IP: `3.85.244.64` \| User: `ubuntu` \| Key: `keys/callinggen-key-prod.pem` |
| **Account** | Morning Tax (User ID: `1`) \| Balance: `4559` credits \| Caller ID: `+19344001617` |
| **LiveKit Server** | URL: `ws://127.0.0.1:7880` \| Key: `APIuLp9KqY3tZxV` \| Secret: `SECnRt8MwP2vLyK4jHgF6dQ1sAbXc5zE` |
| **Telnyx Outbound SIP**| Trunk: `ST_AB44u8gKNDkE` \| User: `livekitny` \| Pass: `Genx@12345` \| Host: `sip.telnyx.com:5060` |
| **Sarvam AI (TTS/STT)** | Key: `sk_au4qv9kl_8T8W705F4a4p5xyeqft6rDzh` \| TTS: `bulbul:v3` (meera) \| STT: `saaras:v2` |
| **DeepSeek AI (LLM)** | Key: `sk-c29baf36177d40d4913511bade1430cf` \| Model: `deepseek-chat` |
| **Database (RDS)** | `postgresql+asyncpg://postgres:GENXREALITY123@callinggen-prodb.ck78yeswelnl.us-east-1.rds.amazonaws.com:5432/callinggen_prodb` |
| **AWS S3 Bucket** | Bucket: `callinggen-recordings` \| Region: `ap-south-2` \| Key: `AKIAXVMJTQYIF2VQKO3G` |

---

## How to Connect to Production EC2

From Windows:
```cmd
production_agent_and_account\keys\ssh_connect.bat
```
Or directly:
```bash
ssh -i "production_agent_and_account/keys/callinggen-key-prod.pem" ubuntu@3.85.244.64
```
