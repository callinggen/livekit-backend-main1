#!/bin/bash
set -e

echo "=== 1. System Updates & Prerequisites ==="
sudo apt-get update -y
sudo apt-get upgrade -y
sudo apt-get install -y python3.12 python3.12-venv python3-pip git nginx curl

echo "=== 2. Installing Docker & Docker Compose ==="
if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker ubuntu
fi

echo "=== 3. Setting up LiveKit Configuration ==="
mkdir -p ~/livekit
cd ~/livekit

cat << 'EOF' > livekit.yaml
port: 7880
bind_addresses:
  - ""

rtc:
  tcp_port: 7881
  port_range_start: 50000
  port_range_end: 60000
  node_ip: 13.232.26.174
  use_external_ip: true

keys:
  devkey: devsecret123456789012345678901234567890

redis:
  address: 127.0.0.1:6379

logging:
  json: false
  level: info
EOF

cat << 'EOF' > sip.yaml
api_key: devkey
api_secret: devsecret123456789012345678901234567890
ws_url: ws://127.0.0.1:7880

sip_host: 0.0.0.0
sip_port: 5060

rtp_port_start: 10000
rtp_port_end: 20000

public_ip: 13.232.26.174

redis:
  address: 127.0.0.1:6379

logging:
  json: false
  level: info
EOF

cat << 'EOF' > docker-compose.yml
version: "3.8"

services:
  redis:
    image: redis:7-alpine
    restart: always
    ports:
      - "127.0.0.1:6379:6379"
    volumes:
      - redis_data:/data

  livekit:
    image: livekit/livekit-server:latest
    command: --config /etc/livekit/livekit.yaml
    restart: always
    ports:
      - "7880:7880"
      - "7881:7881/tcp"
      - "50000-60000:50000-60000/udp"
    volumes:
      - ./livekit.yaml:/etc/livekit/livekit.yaml
    depends_on:
      - redis
    network_mode: host

  sip:
    image: livekit/sip:latest
    command: --config /etc/livekit/sip.yaml
    restart: always
    ports:
      - "5060:5060/udp"
      - "5060:5060/tcp"
      - "10000-20000:10000-20000/udp"
    volumes:
      - ./sip.yaml:/etc/livekit/sip.yaml
    depends_on:
      - livekit
    network_mode: host

volumes:
  redis_data:
EOF

echo "=== 4. Starting LiveKit Containers ==="
sudo docker compose up -d

echo "=== 5. Cloning Backend Repository ==="
rm -rf ~/app
git clone https://github.com/callinggen/livekit-test.git ~/app

echo "=== 6. Setting Up Python Environment ==="
cd ~/app/BACKEND
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "=== 7. Creating Environment File ==="
cat << 'EOF' > ~/app/BACKEND/app/.env
LIVEKIT_URL=ws://13.232.26.174:7880
LIVEKIT_API_KEY=devkey
LIVEKIT_API_SECRET=devsecret123456789012345678901234567890

DEEPSEEK_API_KEY=sk-83f043e9f53f4adb9d99fc2031c4c3e5
SARVAM_API_KEY=sk_7njrwqjx_J1IFf9t2q5pfuvRFi8uKm3vc
BACKEND_URL=http://127.0.0.1:8000
EOF

echo "=== 8. Running DB Migrations ==="
python migrate.py

echo "=== 9. Creating systemd Services ==="

cat << 'EOF' | sudo tee /etc/systemd/system/callinggen-api.service > /dev/null
[Unit]
Description=CallingGen FastAPI Backend
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/app/BACKEND
EnvironmentFile=/home/ubuntu/app/BACKEND/app/.env
ExecStart=/home/ubuntu/app/BACKEND/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

cat << 'EOF' | sudo tee /etc/systemd/system/callinggen-worker.service > /dev/null
[Unit]
Description=CallingGen Campaign Worker
After=callinggen-api.service

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/app/BACKEND
EnvironmentFile=/home/ubuntu/app/BACKEND/app/.env
ExecStart=/home/ubuntu/app/BACKEND/.venv/bin/python -m app.worker
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

cat << 'EOF' | sudo tee /etc/systemd/system/callinggen-agent.service > /dev/null
[Unit]
Description=CallingGen LiveKit Agent
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/app/BACKEND
EnvironmentFile=/home/ubuntu/app/BACKEND/app/.env
ExecStart=/home/ubuntu/app/BACKEND/.venv/bin/python agent.py start
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable callinggen-api callinggen-worker callinggen-agent
sudo systemctl start callinggen-api callinggen-worker callinggen-agent

echo "=== 10. Configuring Nginx Reverse Proxy ==="
cat << 'EOF' | sudo tee /etc/nginx/sites-available/callinggen > /dev/null
server {
    listen 80;
    server_name 13.232.26.174;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
EOF

sudo rm -f /etc/nginx/sites-enabled/default
sudo ln -sf /etc/nginx/sites-available/callinggen /etc/nginx/sites-enabled/
sudo systemctl restart nginx

echo "=== Deployment Finished Successfully! ==="
