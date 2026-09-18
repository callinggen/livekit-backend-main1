from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
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


class SendTextMessageRequest(BaseModel):
    instance_name: str
    number: str
    text: str


@router.post("/send-text")
async def send_text(req: SendTextMessageRequest):
    try:
        data = await service.send_text_message(req.instance_name, req.number, req.text)
        return {"success": True, "data": data}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/webhook")
async def webhook_verification():
    """Webhook verification/health check endpoint."""
    return {"status": "active", "message": "WhatsApp webhook endpoint is ready"}


@router.post("/webhook")
async def handle_whatsapp_webhook(payload: Dict[str, Any], background_tasks: BackgroundTasks):
    """
    Ingests inbound WhatsApp webhooks from Evolution API and triggers the bot pipeline.
    Returns HTTP 200 immediately and processes LLM in the background.
    """
    from .bot_adapter import whatsapp_bot_pipeline

    async def _process_webhook():
        try:
            await whatsapp_bot_pipeline.process_incoming_message(payload)
        except Exception as e:
            print(f"[whatsapp_routes] Error processing webhook async: {e}")

    background_tasks.add_task(_process_webhook)
    return {"success": True, "message": "Webhook queued for processing"}


class BotInteractRequest(BaseModel):
    phone: str
    message: str
    customer_name: Optional[str] = ""
    agent_type: Optional[str] = "Meera (Morning Tax)"


@router.post("/bot/interact")
async def bot_interact(req: BotInteractRequest):
    """
    Direct test / programmatic interface to interact with the bot pipeline for a specific phone number.
    """
    try:
        from .bot_adapter import whatsapp_bot_pipeline
        response_text = await whatsapp_bot_pipeline.generate_response(
            phone=req.phone,
            user_message=req.message,
            customer_name=req.customer_name or "",
            agent_type=req.agent_type or "Meera (Morning Tax)",
        )
        return {"success": True, "reply": response_text, "phone": req.phone}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class WebhookConfigRequest(BaseModel):
    instance_name: Optional[str] = "callinggen"
    webhook_url: Optional[str] = "http://host.docker.internal:8000/api/whatsapp/webhook"


@router.post("/webhook/configure")
async def configure_webhook(req: WebhookConfigRequest):
    """Configures the webhook URL in Evolution API so incoming messages trigger the bot."""
    try:
        instance = req.instance_name or "callinggen"
        url = req.webhook_url or "http://host.docker.internal:8000/api/whatsapp/webhook"
        data = await service.set_webhook(instance, url)
        return {"success": True, "data": data}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


