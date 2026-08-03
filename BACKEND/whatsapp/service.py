import httpx
from typing import Dict, Any, Optional
from .config import EVOLUTION_API_URL, EVOLUTION_API_KEY

def get_headers() -> Dict[str, str]:
    if not EVOLUTION_API_KEY:
        raise ValueError("EVOLUTION_API_KEY is not set")
    return {
        "apikey": EVOLUTION_API_KEY,
        "Content-Type": "application/json"
    }

async def create_instance(instance_name: str) -> Dict[str, Any]:
    if not EVOLUTION_API_URL:
        raise ValueError("EVOLUTION_API_URL is not set")
        
    url = f"{EVOLUTION_API_URL}/instance/create"
    payload = {
        "instanceName": instance_name,
        "qrcode": True,
        "integration": "WHATSAPP-BAILEYS"
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, headers=get_headers())
        # If it already exists, Evolution API might return 400 or a specific error.
        # We pass the raw response up to the router to handle.
        response.raise_for_status()
        return response.json()

async def get_qr_code(instance_name: str) -> Dict[str, Any]:
    if not EVOLUTION_API_URL:
        raise ValueError("EVOLUTION_API_URL is not set")
        
    url = f"{EVOLUTION_API_URL}/instance/connect/{instance_name}"
    
    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=get_headers())
        response.raise_for_status()
        return response.json()

async def get_connection_status(instance_name: str) -> Dict[str, Any]:
    if not EVOLUTION_API_URL:
        raise ValueError("EVOLUTION_API_URL is not set")
        
    url = f"{EVOLUTION_API_URL}/instance/connectionState/{instance_name}"
    
    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=get_headers())
        response.raise_for_status()
        return response.json()

async def disconnect_instance(instance_name: str) -> Dict[str, Any]:
    if not EVOLUTION_API_URL:
        raise ValueError("EVOLUTION_API_URL is not set")
        
    url = f"{EVOLUTION_API_URL}/instance/logout/{instance_name}"
    
    async with httpx.AsyncClient() as client:
        response = await client.delete(url, headers=get_headers())
        response.raise_for_status()
        return response.json()

async def get_chats(instance_name: str) -> list:
    if not EVOLUTION_API_URL:
        raise ValueError("EVOLUTION_API_URL is not set")
        
    url = f"{EVOLUTION_API_URL}/chat/findChats/{instance_name}"
    
    async with httpx.AsyncClient() as client:
        # Some versions use GET, some use POST for findChats. Testing showed POST with empty JSON works, but usually it's POST if we pass filters. 
        # Actually in test script I used POST with empty json and it worked.
        response = await client.post(url, headers=get_headers(), json={})
        response.raise_for_status()
        return response.json()

async def get_messages(instance_name: str, remote_jid: str) -> dict:
    if not EVOLUTION_API_URL:
        raise ValueError("EVOLUTION_API_URL is not set")
        
    url = f"{EVOLUTION_API_URL}/chat/findMessages/{instance_name}"
    
    async with httpx.AsyncClient() as client:
        # We request a higher limit since Evolution API sometimes ignores the where clause
        # and returns a mixed feed, which we filter on the frontend.
        payload = {"limit": 500, "where": {"remoteJid": remote_jid}}
        response = await client.post(url, headers=get_headers(), json=payload)
        response.raise_for_status()
        return response.json()

