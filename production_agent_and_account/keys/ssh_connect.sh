#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
chmod 400 "${SCRIPT_DIR}/callinggen-key-prod.pem"
ssh -i "${SCRIPT_DIR}/callinggen-key-prod.pem" ubuntu@3.85.244.64
