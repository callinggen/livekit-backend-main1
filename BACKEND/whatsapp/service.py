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

async def get_qr_code(instance_name: str, number: Optional[str] = None) -> Dict[str, Any]:
    if not EVOLUTION_API_URL:
        raise ValueError("EVOLUTION_API_URL is not set")
        
    if number and number.strip():
        clean_num = "".join(c for c in number if c.isdigit())
        if len(clean_num) == 10:
            clean_num = "91" + clean_num
        url = f"{EVOLUTION_API_URL}/instance/connect/{instance_name}?number={clean_num}"
    else:
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
        
    url_logout = f"{EVOLUTION_API_URL}/instance/logout/{instance_name}"
    url_del = f"{EVOLUTION_API_URL}/instance/delete/{instance_name}"
    
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            await client.delete(url_logout, headers=get_headers())
        except Exception:
            pass
            
        try:
            await client.delete(url_del, headers=get_headers())
        except Exception:
            pass
            
        try:
            await client.post(f"{EVOLUTION_API_URL}/instance/create", headers=get_headers(), json={
                "instanceName": instance_name,
                "qrcode": True,
                "integration": "WHATSAPP-BAILEYS"
            })
        except Exception:
            pass
            
        return {"status": "disconnected"}

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

async def get_messages(instance_name: str, remote_jid: str) -> Dict[str, Any]:
    """Retrieve messages for a specific remoteJid/chat, filtered strictly to prevent mixing."""
    if not EVOLUTION_API_URL:
        raise ValueError("EVOLUTION_API_URL is not set")

    url = f"{EVOLUTION_API_URL}/chat/findMessages/{instance_name}"
    clean_target = "".join(c for c in (remote_jid or "") if c.isdigit())

    async with httpx.AsyncClient(timeout=15.0) as client:
        payload = {"limit": 1000, "where": {"remoteJid": remote_jid}}
        response = await client.post(url, headers=get_headers(), json=payload)
        response.raise_for_status()
        raw_data = response.json()

        records = (
            raw_data.get("messages", {}).get("records", [])
            if isinstance(raw_data, dict)
            else (raw_data if isinstance(raw_data, list) else [])
        )

        filtered_records = []
        for r in records:
            if not isinstance(r, dict):
                continue
            key = r.get("key", {})
            r_jid = str(key.get("remoteJid") or "")
            r_alt = str(key.get("remoteJidAlt") or "")
            r_part = str(key.get("participant") or "")

            # Strict isolation to this chat
            if (
                r_jid == remote_jid
                or (clean_target and len(clean_target) >= 7 and clean_target in r_jid)
                or (r_alt and (r_alt == remote_jid or (clean_target and len(clean_target) >= 7 and clean_target in r_alt)))
                or (r_part and (r_part == remote_jid or (clean_target and len(clean_target) >= 7 and clean_target in r_part)))
            ):
                filtered_records.append(r)

        return {"messages": {"records": filtered_records}, "total": len(filtered_records)}


async def send_text_message(instance_name: str, number: str, text: str) -> Dict[str, Any]:
    """Send a plain text message via Evolution API."""
    if not EVOLUTION_API_URL:
        raise ValueError("EVOLUTION_API_URL is not set")

    clean_number = "".join(c for c in number if c.isdigit())
    url = f"{EVOLUTION_API_URL}/message/sendText/{instance_name}"
    payload = {
        "number": clean_number,
        "text": text,
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(url, headers=get_headers(), json=payload)
        response.raise_for_status()
        return response.json()


async def send_media_message(
    instance_name: str,
    number: str,
    media_url: str,
    media_type: str = "document",
    mimetype: str = "application/pdf",
    caption: Optional[str] = None,
    file_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Send media (document/image/pdf) via Evolution API."""
    if not EVOLUTION_API_URL:
        raise ValueError("EVOLUTION_API_URL is not set")

    clean_number = "".join(c for c in number if c.isdigit())
    url = f"{EVOLUTION_API_URL}/message/sendMedia/{instance_name}"
    payload: Dict[str, Any] = {
        "number": clean_number,
        "mediatype": media_type,
        "mimetype": mimetype,
        "media": media_url,
    }
    if caption:
        payload["caption"] = caption
    if file_name:
        payload["fileName"] = file_name

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(url, headers=get_headers(), json=payload)
        response.raise_for_status()
        return response.json()

