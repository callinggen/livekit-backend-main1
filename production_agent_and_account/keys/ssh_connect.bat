@echo off
REM Connect to Production EC2 Server
ssh -i "%~dp0callinggen-key-prod.pem" ubuntu@3.85.244.64
