from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import re

from app.database import get_db
from app.models.contact import Contact
from . import service
from .config import resolve_instance_name

router = APIRouter()

class InstanceRequest(BaseModel):
    instance_name: Optional[str] = None

@router.post("/instance")
async def create_instance(req: InstanceRequest):
    try:
        inst_name = resolve_instance_name(req.instance_name)
        data = await service.create_instance(inst_name)
        return {"success": True, "data": data}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/qr")
async def get_qr(instance_name: Optional[str] = Query(None), number: Optional[str] = Query(None)):
    try:
        inst_name = resolve_instance_name(instance_name)
        data = await service.get_qr_code(inst_name, number=number)
        return {"success": True, "data": data}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/status")
async def get_status(instance_name: Optional[str] = Query(None)):
    try:
        inst_name = resolve_instance_name(instance_name)
        data = await service.get_connection_status(inst_name)
        return {"success": True, "data": data}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/info")
async def get_info(instance_name: Optional[str] = Query(None)):
    try:
        inst_name = resolve_instance_name(instance_name)
        status_data = await service.get_connection_status(inst_name)
        state = status_data.get("instance", {}).get("state", "disconnected")
        phone = status_data.get("instance", {}).get("owner", "Unknown")
        
        info = {
            "instance_name": inst_name,
            "connected_phone": phone,
            "last_connected": "Unknown",
            "status": state
        }
        return {"success": True, "data": info}
    except Exception as e:
        return {"success": True, "data": {"instance_name": resolve_instance_name(instance_name), "connected_phone": None, "status": "disconnected"}}

@router.delete("/logout")
async def logout(instance_name: Optional[str] = Query(None)):
    try:
        inst_name = resolve_instance_name(instance_name)
        data = await service.disconnect_instance(inst_name)
        return {"success": True, "data": data}
    except Exception as e:
        return {"success": True, "data": {"status": "disconnected"}}

def clean_digits(val: Optional[str]) -> str:
    if not val:
        return ""
    return re.sub(r"\D", "", str(val))

@router.get("/chats")
async def get_chats(
    instance_name: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    try:
        inst_name = resolve_instance_name(instance_name)
        raw_chats = await service.get_chats(inst_name)
        
        # Load local database contacts to enrich saved names
        db_contacts_map: Dict[str, str] = {}
        try:
            db_res = await db.execute(select(Contact.phone, Contact.name, Contact.customer_name))
            rows = db_res.all()
            for r_phone, r_name, r_cust in rows:
                digits = clean_digits(r_phone)
                best_name = (r_name or r_cust or "").strip()
                if digits and best_name:
                    if len(digits) >= 10:
                        db_contacts_map[digits[-10:]] = best_name
                    db_contacts_map[digits] = best_name
        except Exception as db_err:
            print(f"Error fetching DB contacts for WhatsApp sync: {db_err}")

        # Fetch WhatsApp contacts from Evolution API
        evo_contacts_map: Dict[str, Dict[str, Any]] = {}
        try:
            raw_contacts = await service.get_contacts(inst_name)
            if isinstance(raw_contacts, list):
                for c in raw_contacts:
                    if isinstance(c, dict):
                        jid = str(c.get("id") or c.get("remoteJid") or "")
                        c_num = clean_digits(c.get("number") or jid)
                        c_name = c.get("name") or c.get("pushName") or c.get("verifiedName") or ""
                        c_pic = c.get("profilePicUrl") or c.get("profilePictureUrl") or None
                        info = {"name": c_name, "profilePicUrl": c_pic, "number": c_num}
                        if jid:
                            evo_contacts_map[jid] = info
                        if c_num:
                            evo_contacts_map[c_num] = info
                            if len(c_num) >= 10:
                                evo_contacts_map[c_num[-10:]] = info
        except Exception:
            pass

        seen_jids = set()
        enriched_chats = []

        if isinstance(raw_chats, list):
            for chat in raw_chats:
                if not isinstance(chat, dict):
                    continue
                jid = str(chat.get("remoteJid") or chat.get("id") or "")
                
                # Ignore WhatsApp Status broadcast and Group chats
                if "status@broadcast" in jid or "@g.us" in jid:
                    continue

                raw_digits = clean_digits(jid)
                if not raw_digits and not jid:
                    continue

                seen_jids.add(jid)
                if len(raw_digits) >= 10:
                    seen_jids.add(raw_digits[-10:])
                seen_jids.add(raw_digits)

                # Resolve real phone number and name
                resolved_name = None
                resolved_phone = None
                resolved_pic = chat.get("profilePicUrl") or None

                # 1. Check if contact info exists in Evolution contact cache
                contact_info = evo_contacts_map.get(jid) or (evo_contacts_map.get(raw_digits[-10:]) if len(raw_digits) >= 10 else None)
                if contact_info:
                    if contact_info.get("name"):
                        resolved_name = contact_info["name"]
                    if contact_info.get("profilePicUrl") and not resolved_pic:
                        resolved_pic = contact_info["profilePicUrl"]
                    if contact_info.get("number") and len(contact_info["number"]) <= 14:
                        resolved_phone = contact_info["number"]

                # 2. Match with our local DB contacts (Highest priority for saved names!)
                if not resolved_phone and "@s.whatsapp.net" in jid:
                    resolved_phone = raw_digits

                check_num = resolved_phone or raw_digits
                if check_num and len(check_num) >= 10:
                    last10 = check_num[-10:]
                    if last10 in db_contacts_map:
                        resolved_name = db_contacts_map[last10]
                    elif check_num in db_contacts_map:
                        resolved_name = db_contacts_map[check_num]

                # 3. Fallback to chat pushName / name if not in DB
                if not resolved_name:
                    resolved_name = chat.get("pushName") or chat.get("name") or chat.get("verifiedName")

                # If phone was a 15+ digit LID and unresolved, keep fallback
                if not resolved_phone:
                    if "@s.whatsapp.net" in jid:
                        resolved_phone = raw_digits
                    elif len(raw_digits) <= 14:
                        resolved_phone = raw_digits
                    else:
                        resolved_phone = None

                # Attach enriched metadata to chat object
                chat["resolved_name"] = resolved_name
                chat["resolved_phone"] = resolved_phone
                if resolved_pic:
                    chat["profilePicUrl"] = resolved_pic

                enriched_chats.append(chat)

        # Merge Evolution address book contacts not in chats
        if isinstance(raw_contacts, list):
            for c in raw_contacts:
                if not isinstance(c, dict):
                    continue
                jid = str(c.get("id") or c.get("remoteJid") or "")
                c_num = clean_digits(c.get("number") or jid)
                if not c_num or jid in seen_jids or c_num in seen_jids or (len(c_num) >= 10 and c_num[-10:] in seen_jids):
                    continue

                seen_jids.add(jid)
                seen_jids.add(c_num)
                if len(c_num) >= 10:
                    seen_jids.add(c_num[-10:])

                c_name = c.get("name") or c.get("pushName") or c.get("verifiedName") or ""
                if len(c_num) >= 10 and c_num[-10:] in db_contacts_map:
                    c_name = db_contacts_map[c_num[-10:]]
                elif c_num in db_contacts_map:
                    c_name = db_contacts_map[c_num]

                enriched_chats.append({
                    "remoteJid": jid if "@s.whatsapp.net" in jid else f"{c_num}@s.whatsapp.net",
                    "resolved_name": c_name or c_num,
                    "resolved_phone": c_num,
                    "profilePicUrl": c.get("profilePicUrl") or c.get("profilePictureUrl") or None,
                    "unreadCount": 0,
                    "lastMessage": {
                        "message": {"conversation": "Contact synced from WhatsApp"}
                    }
                })

        # Merge local DB contacts not in chats
        try:
            db_res_all = await db.execute(select(Contact.phone, Contact.name, Contact.customer_name))
            db_rows = db_res_all.all()
            for r_phone, r_name, r_cust in db_rows:
                digits = clean_digits(r_phone)
                if not digits or digits in seen_jids or (len(digits) >= 10 and digits[-10:] in seen_jids):
                    continue

                seen_jids.add(digits)
                if len(digits) >= 10:
                    seen_jids.add(digits[-10:])

                contact_name = (r_name or r_cust or "").strip() or digits
                full_num = digits if len(digits) > 10 else f"91{digits}"
                enriched_chats.append({
                    "remoteJid": f"{full_num}@s.whatsapp.net",
                    "resolved_name": contact_name,
                    "resolved_phone": full_num,
                    "profilePicUrl": None,
                    "unreadCount": 0,
                    "lastMessage": {
                        "message": {"conversation": "Tap to start conversation"}
                    }
                })
        except Exception as e:
            print(f"Error merging DB contacts: {e}")

        return {"success": True, "data": enriched_chats}
    except Exception as e:
        print(f"Error in get_chats endpoint: {e}")
        return {"success": True, "data": []}

@router.get("/messages")
async def get_messages(remote_jid: str = Query(...), instance_name: Optional[str] = Query(None)):
    try:
        inst_name = resolve_instance_name(instance_name)
        data = await service.get_messages(inst_name, remote_jid)
        return {"success": True, "data": data}
    except Exception as e:
        return {"success": True, "data": {"messages": {"records": []}, "total": 0}}

@router.get("/profile-picture")
async def get_profile_picture(number: str = Query(...), instance_name: Optional[str] = Query(None)):
    try:
        inst_name = resolve_instance_name(instance_name)
        data = await service.get_profile_picture(inst_name, number)
        return {"success": True, "data": data}
    except Exception as e:
        return {"success": True, "data": {"profilePictureUrl": None}}

class SendTextMessageRequest(BaseModel):
    instance_name: Optional[str] = None
    number: str
    text: str

@router.post("/send-text")
async def send_text(req: SendTextMessageRequest):
    try:
        inst_name = resolve_instance_name(req.instance_name)
        data = await service.send_text_message(inst_name, req.number, req.text)
        return {"success": True, "data": data}
    except Exception as e:
        detail_msg = str(e)
        if hasattr(e, "response") and getattr(e, "response", None) is not None:
            try:
                raw_text = e.response.text
                if "exists': false" in raw_text.lower() or 'exists": false' in raw_text.lower() or '"exists":false' in raw_text.lower():
                    detail_msg = f"Phone number {req.number} is not registered on WhatsApp"
                else:
                    resp_json = e.response.json()
                    resp_inner = resp_json.get("response", {}) if isinstance(resp_json, dict) else {}
                    msg = (resp_inner.get("message") if isinstance(resp_inner, dict) else None) or (resp_json.get("message") if isinstance(resp_json, dict) else None)
                    if isinstance(msg, list) and len(msg) > 0:
                        detail_msg = str(msg[0])
                    elif msg:
                        detail_msg = str(msg)
            except Exception:
                pass
        raise HTTPException(status_code=400, detail=detail_msg)

class SendMediaMessageRequest(BaseModel):
    instance_name: Optional[str] = None
    number: str
    media_url: str
    media_type: Optional[str] = "document"
    caption: Optional[str] = None
    file_name: Optional[str] = None
    mimetype: Optional[str] = "application/pdf"

@router.post("/send-media")
async def send_media(req: SendMediaMessageRequest):
    try:
        inst_name = resolve_instance_name(req.instance_name)
        mimetype = req.mimetype
        if not mimetype:
            if req.media_type == "image":
                mimetype = "image/jpeg"
            else:
                mimetype = "application/pdf"

        data = await service.send_media_message(
            instance_name=inst_name,
            number=req.number,
            media_url=req.media_url,
            media_type=req.media_type or "document",
            mimetype=mimetype,
            caption=req.caption,
            file_name=req.file_name or "Document.pdf",
        )
        return {"success": True, "data": data}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


