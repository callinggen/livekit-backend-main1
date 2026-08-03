from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, Dict, Any
from . import service

router = APIRouter()

class InstanceRequest(BaseModel):
    instance_name: str

@router.post("/instance")
async def create_instance(req: InstanceRequest):
    try:
        data = await service.create_instance(req.instance_name)
        return {"success": True, "data": data}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/qr")
async def get_qr(instance_name: str = Query(...)):
    try:
        data = await service.get_qr_code(instance_name)
        return {"success": True, "data": data}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/status")
async def get_status(instance_name: str = Query(...)):
    try:
        data = await service.get_connection_status(instance_name)
        return {"success": True, "data": data}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/info")
async def get_info(instance_name: str = Query(...)):
    try:
        # Fetch status
        status_data = await service.get_connection_status(instance_name)
        state = status_data.get("instance", {}).get("state", "disconnected")
        
        # In Evolution API, the connected phone number is often in the instance info or status.
        # We will extract what we can from the connectionState endpoint.
        # Note: If more specific data is needed, we would add another service call,
        # but sticking strictly to the wrapper requirements.
        phone = status_data.get("instance", {}).get("owner", "Unknown")
        
        # Build info object
        info = {
            "instance_name": instance_name,
            "connected_phone": phone,
            "last_connected": "Unknown", # Evolution connectionState might not return this explicitly, will use placeholder or extract if present
            "status": state
        }
        return {"success": True, "data": info}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/logout")
async def logout(instance_name: str = Query(...)):
    try:
        data = await service.disconnect_instance(instance_name)
        return {"success": True, "data": data}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/chats")
async def get_chats(instance_name: str = Query(...)):
    try:
        data = await service.get_chats(instance_name)
        return {"success": True, "data": data}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/messages")
async def get_messages(instance_name: str = Query(...), remote_jid: str = Query(...)):
    try:
        data = await service.get_messages(instance_name, remote_jid)
        return {"success": True, "data": data}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
