#!/bin/bash
set -e

echo "=== 1. Installing Node.js (v20) ==="
if ! command -v node &> /dev/null; then
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
    sudo apt-get install -y nodejs
fi

echo "=== 2. Cloning Frontend Repository ==="
rm -rf ~/frontend
git clone https://github.com/callinggen/livekit-forntend-main1.git ~/frontend

echo "=== 3. Installing Frontend Dependencies ==="
cd ~/frontend
npm install

echo "=== 4. Configuring Env and Building Next.js ==="
echo "NEXT_PUBLIC_API_URL=http://13.232.26.174" > .env.local
npm run build

echo "=== 5. Creating Frontend systemd Service ==="
cat << 'EOF' | sudo tee /etc/systemd/system/callinggen-frontend.service > /dev/null
[Unit]
Description=CallingGen Next.js Frontend
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/frontend
ExecStart=/usr/bin/npm start
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable callinggen-frontend
sudo systemctl restart callinggen-frontend

echo "=== 6. Updating Nginx Proxy Rules ==="
cat << 'EOF' | sudo tee /etc/nginx/sites-available/callinggen > /dev/null
server {
    listen 80;
    server_name 13.232.26.174;

    # Backend API calls prefix
    location /api {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Frontend Next.js app
    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF

sudo systemctl restart nginx

echo "=== 7. Installing livekit-cli ==="
if ! command -v livekit-cli &> /dev/null; then
    curl -sSL https://get.livekit.io/cli | bash
    sudo mv livekit-cli /usr/local/bin/
fi

echo "=== 8. Creating SIP Trunk for Vobiz ==="
# Registering the Vobiz trunk with local LiveKit server
livekit-cli sip trunk create \
  --url ws://localhost:7880 \
  --api-key devkey \
  --api-secret devsecret123456789012345678901234567890 \
  --outbound-address 9beeb252.sip.vobiz.ai \
  --outbound-username livekit-deployment \
  --outbound-password Genx@12345

echo "=== Done deploying frontend & trunk! ==="
