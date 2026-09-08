import httpx
from typing import Dict, Any, Optional
from .config import get_api_url, get_api_key

def get_headers() -> Dict[str, str]:
    key = get_api_key()
    if not key:
        raise ValueError("EVOLUTION_API_KEY is not set")
    return {
        "apikey": key,
        "Content-Type": "application/json"
    }

async def create_instance(instance_name: str, number: Optional[str] = None) -> Dict[str, Any]:
    api_url = get_api_url()
    if not api_url:
        raise ValueError("EVOLUTION_API_URL is not set")
        
    url = f"{api_url}/instance/create"
    payload: Dict[str, Any] = {
        "instanceName": instance_name,
        "qrcode": True,
        "integration": "WHATSAPP-BAILEYS"
    }
    if number and number.strip():
        clean_num = "".join(c for c in number if c.isdigit())
        if len(clean_num) == 10:
            clean_num = "91" + clean_num
        payload["number"] = clean_num

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(url, json=payload, headers=get_headers())
        if response.status_code in (200, 201):
            return response.json()
        return response.json() if response.status_code < 500 else {}


async def get_qr_code(instance_name: str, number: Optional[str] = None) -> Dict[str, Any]:
    api_url = get_api_url()
    if not api_url:
        raise ValueError("EVOLUTION_API_URL is not set")
        
    clean_num = None
    if number and number.strip():
        clean_num = "".join(c for c in number if c.isdigit())
        if len(clean_num) == 10:
            clean_num = "91" + clean_num

    async with httpx.AsyncClient(timeout=20.0) as client:
        # For pairing code: we must first logout & delete the existing session
        # (if it's connected), then create a fresh one with the phone number.
        if clean_num:
            # 1. Logout the current session
            try:
                await client.delete(f"{api_url}/instance/logout/{instance_name}", headers=get_headers())
            except Exception:
                pass
            # 2. Delete the instance entirely
            try:
                await client.delete(f"{api_url}/instance/delete/{instance_name}", headers=get_headers())
            except Exception:
                pass
            # 3. Re-create with phone number to get a pairing code
            create_payload = {
                "instanceName": instance_name,
                "qrcode": True,
                "number": clean_num,
                "integration": "WHATSAPP-BAILEYS"
            }
            create_res = await client.post(
                f"{api_url}/instance/create",
                json=create_payload,
                headers=get_headers(),
            )
            if create_res.status_code in (200, 201):
                c_data = create_res.json()
                # Normalize pairing code location
                if c_data.get("qrcode", {}).get("pairingCode"):
                    c_data["pairingCode"] = c_data["qrcode"]["pairingCode"]
                if c_data.get("pairingCode"):
                    return c_data

            # 4. Fallback: fetch connect endpoint with number
            conn_res = await client.get(
                f"{api_url}/instance/connect/{instance_name}?number={clean_num}",
                headers=get_headers(),
            )
            if conn_res.status_code == 200:
                data = conn_res.json()
                if data.get("qrcode", {}).get("pairingCode"):
                    data["pairingCode"] = data["qrcode"]["pairingCode"]
                return data
            return {"error": "Could not generate pairing code", "pairingCode": None}

        # QR code path (no phone number)
        url = f"{api_url}/instance/connect/{instance_name}"
        try:
            response = await client.get(url, headers=get_headers())
            data: Dict[str, Any] = response.json() if response.status_code == 200 else {}
        except Exception:
            data = {}

        # If instance does not exist (404) or has no QR code, create instance first
        b64 = data.get("base64") or data.get("qrcode", {}).get("base64") or data.get("code")
        if not b64:
            try:
                create_payload = {
                    "instanceName": instance_name,
                    "qrcode": True,
                    "integration": "WHATSAPP-BAILEYS"
                }
                create_res = await client.post(
                    f"{api_url}/instance/create",
                    json=create_payload,
                    headers=get_headers(),
                )
                if create_res.status_code in (200, 201):
                    c_data = create_res.json()
                    if c_data.get("qrcode", {}).get("base64"):
                        c_data["base64"] = c_data["qrcode"]["base64"]
                    if c_data.get("qrcode", {}).get("code"):
                        c_data["code"] = c_data["qrcode"]["code"]
                    if c_data.get("base64"):
                        return c_data

                # Re-fetch connect after create
                conn_res = await client.get(url, headers=get_headers())
                if conn_res.status_code == 200:
                    data = conn_res.json()
            except Exception as e:
                print(f"Error creating instance for QR: {e}")

        # Normalize base64 & pairingCode from nested qrcode if present
        if not data.get("base64") and data.get("qrcode", {}).get("base64"):
            data["base64"] = data.get("qrcode", {}).get("base64")
        if not data.get("code") and data.get("qrcode", {}).get("code"):
            data["code"] = data.get("qrcode", {}).get("code")
        if not data.get("pairingCode") and data.get("qrcode", {}).get("pairingCode"):
            data["pairingCode"] = data["qrcode"]["pairingCode"]

        return data

async def get_connection_status(instance_name: str) -> Dict[str, Any]:
    api_url = get_api_url()
    if not api_url:
        raise ValueError("EVOLUTION_API_URL is not set")
        
    url = f"{api_url}/instance/connectionState/{instance_name}"
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(url, headers=get_headers())
        
        # If instance doesn't exist (404) or unauthorized – treat as disconnected
        if response.status_code in (404, 401):
            return {"instance": {"instanceName": instance_name, "state": "close"}}
        
        try:
            response.raise_for_status()
        except Exception:
            return {"instance": {"instanceName": instance_name, "state": "close"}}
        
        data = response.json()
        
        try:
            r_inst = await client.get(f"{api_url}/instance/fetchInstances", headers=get_headers())
            if r_inst.status_code == 200:
                for inst in r_inst.json():
                    if inst.get("name") == instance_name:
                        owner = inst.get("ownerJid") or inst.get("number")
                        profile_name = inst.get("profileName")
                        if isinstance(data.get("instance"), dict):
                            data["instance"]["owner"] = owner
                            data["instance"]["profileName"] = profile_name
                        else:
                            data["owner"] = owner
                            data["profileName"] = profile_name
                        break
        except Exception:
            pass
            
        return data

async def disconnect_instance(instance_name: str) -> Dict[str, Any]:
    api_url = get_api_url()
    if not api_url:
        raise ValueError("EVOLUTION_API_URL is not set")
        
    url_logout = f"{api_url}/instance/logout/{instance_name}"
    url_del = f"{api_url}/instance/delete/{instance_name}"
    
    async with httpx.AsyncClient(timeout=15.0) as client:
        # 1. Logout the WhatsApp session
        try:
            await client.delete(url_logout, headers=get_headers())
        except Exception:
            pass
            
        # 2. Delete the instance so it does NOT re-appear as connected
        try:
            await client.delete(url_del, headers=get_headers())
        except Exception:
            pass

        return {"status": "disconnected"}

async def get_chats(instance_name: str) -> list:
    api_url = get_api_url()
    if not api_url:
        return []
        
    url = f"{api_url}/chat/findChats/{instance_name}"
    
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, headers=get_headers(), json={})
            if response.status_code == 200:
                return response.json()
            res_get = await client.get(url, headers=get_headers())
            if res_get.status_code == 200:
                return res_get.json()
            return []
    except Exception as e:
        print(f"Error fetching chats: {e}")
        return []

async def get_contacts(instance_name: str) -> list:
    api_url = get_api_url()
    if not api_url:
        return []
    url = f"{api_url}/chat/findContacts/{instance_name}"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.post(url, headers=get_headers(), json={})
            if res.status_code == 200:
                return res.json()
            res_get = await client.get(url, headers=get_headers())
            if res_get.status_code == 200:
                return res_get.json()
            return []
    except Exception:
        return []

async def get_profile_picture(instance_name: str, number: str) -> Dict[str, Any]:
    api_url = get_api_url()
    if not api_url:
        return {"profilePictureUrl": None}
        
    url = f"{api_url}/chat/fetchProfilePictureUrl/{instance_name}"
    clean_num = "".join(c for c in (number or "") if c.isdigit())
    if not clean_num:
        return {"profilePictureUrl": None}
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, headers=get_headers(), json={"number": clean_num})
            if response.status_code == 200:
                data = response.json()
                return data if isinstance(data, dict) else {"profilePictureUrl": None}
            return {"profilePictureUrl": None}
    except Exception:
        return {"profilePictureUrl": None}

async def get_messages(instance_name: str, remote_jid: str) -> Dict[str, Any]:
    """Retrieve messages for a specific remoteJid/chat, sorted chronologically."""
    api_url = get_api_url()
    if not api_url:
        return {"messages": {"records": []}, "total": 0}

    url = f"{api_url}/chat/findMessages/{instance_name}"
    clean_target = "".join(c for c in (remote_jid or "") if c.isdigit())
    target_10 = clean_target[-10:] if len(clean_target) >= 10 else clean_target

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            payload = {"limit": 1000, "where": {"remoteJid": remote_jid}}
            response = await client.post(url, headers=get_headers(), json=payload)
            if response.status_code != 200:
                return {"messages": {"records": []}, "total": 0}
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

                # Match by remoteJid or phone digits (including 10-digit suffix)
                matches = (
                    r_jid == remote_jid
                    or (target_10 and target_10 in r_jid)
                    or (r_alt and (r_alt == remote_jid or (target_10 and target_10 in r_alt)))
                    or (r_part and (r_part == remote_jid or (target_10 and target_10 in r_part)))
                )
                if matches:
                    filtered_records.append(r)

            # Sort chronologically (oldest at top, latest at bottom)
            filtered_records.sort(key=lambda x: int(x.get("messageTimestamp") or x.get("timestamp") or 0))

            return {"messages": {"records": filtered_records}, "total": len(filtered_records)}
    except Exception as e:
        print(f"Error fetching messages: {e}")
        return {"messages": {"records": []}, "total": 0}

async def send_text_message(instance_name: str, number: str, text: str) -> Dict[str, Any]:
    """Send a plain text message via Evolution API."""
    api_url = get_api_url()
    if not api_url:
        raise ValueError("EVOLUTION_API_URL is not set")

    clean_number = "".join(c for c in number if c.isdigit())
    if len(clean_number) == 10:
        clean_number = "91" + clean_number

    url = f"{api_url}/message/sendText/{instance_name}"
    payload = {
        "number": clean_number,
        "text": text,
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(url, headers=get_headers(), json=payload)
        response.raise_for_status()
        return response.json()

import os
import base64
import urllib.parse

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOAD_DIR = os.path.join(BACKEND_DIR, "uploads", "materials")

async def resolve_media_to_base64(media_input: str) -> str:
    """
    Resolve a media reference (local path, relative URL, backend URL, or data URI)
    to a raw Base64 string that Evolution API can transmit without outbound stream downloads.
    """
    if not media_input:
        return media_input

    # 1. If already data URI (e.g. data:image/png;base64,...), extract raw base64
    if media_input.startswith("data:") and ";base64," in media_input:
        return media_input.split(";base64,", 1)[1]

    # 2. Check if it's already a raw base64 string
    if (
        not media_input.startswith("http://")
        and not media_input.startswith("https://")
        and not media_input.startswith("/")
        and not ("\\" in media_input)
        and len(media_input) > 200
        and not (" " in media_input)
    ):
        return media_input

    # 3. Extract filename if it matches /materials/file/{filename} or similar
    url_path = urllib.parse.urlparse(media_input).path if ("://" in media_input) else media_input
    extracted_name = os.path.basename(url_path)

    # Check potential local paths
    candidates = [
        media_input,
        os.path.join(UPLOAD_DIR, extracted_name),
        os.path.join(BACKEND_DIR, media_input.lstrip("/\\")),
        os.path.join(UPLOAD_DIR, media_input.lstrip("/\\")),
    ]

    for cand in candidates:
        if os.path.isfile(cand):
            try:
                with open(cand, "rb") as f:
                    return base64.b64encode(f.read()).decode("utf-8")
            except Exception as read_err:
                print(f"[EvolutionService] Failed reading local media file '{cand}': {read_err}")

    # 4. If it is an HTTP/HTTPS URL, download via httpx in Python and convert to base64
    if media_input.startswith("http://") or media_input.startswith("https://"):
        try:
            async with httpx.AsyncClient(timeout=25.0, verify=False) as client:
                res = await client.get(media_input)
                if res.status_code == 200:
                    return base64.b64encode(res.content).decode("utf-8")
                else:
                    print(f"[EvolutionService] Remote media download status {res.status_code} for {media_input}")
        except Exception as dl_err:
            print(f"[EvolutionService] Remote media download failed for {media_input}: {dl_err}")

    # Fallback: return as-is
    return media_input


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
    api_url = get_api_url()
    if not api_url:
        raise ValueError("EVOLUTION_API_URL is not set")

    clean_number = "".join(c for c in number if c.isdigit())
    if len(clean_number) == 10:
        clean_number = "91" + clean_number

    # Convert media to raw Base64 payload so Evolution API avoids outbound stream fetch failures
    resolved_media = await resolve_media_to_base64(media_url)

    url = f"{api_url}/message/sendMedia/{instance_name}"
    payload: Dict[str, Any] = {
        "number": clean_number,
        "mediatype": media_type,
        "mimetype": mimetype,
        "media": resolved_media,
    }
    if caption:
        payload["caption"] = caption
    if file_name:
        payload["fileName"] = file_name

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(url, headers=get_headers(), json=payload)
        response.raise_for_status()
        return response.json()
