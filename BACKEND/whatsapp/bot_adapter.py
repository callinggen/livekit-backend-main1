import asyncio
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from dotenv import load_dotenv

from app.database import AsyncSessionLocal
from app.models.agent import Agent as AgentModel
from whatsapp import service as evolution_service
from whatsapp.config import EVOLUTION_INSTANCE_NAME

load_dotenv()


class DeduplicationStore:
    """In-memory TTL cache to track processed WhatsApp message IDs and prevent duplicate processing."""

    def __init__(self, ttl_seconds: int = 300, max_size: int = 5000):
        self._seen: Dict[str, float] = {}
        self.ttl_seconds = ttl_seconds
        self.max_size = max_size

    def is_duplicate(self, message_id: str) -> bool:
        if not message_id:
            return False

        now = time.time()
        self._clean_expired(now)

        if message_id in self._seen:
            return True

        # Track new message ID
        if len(self._seen) >= self.max_size:
            # Drop oldest 20%
            sorted_items = sorted(self._seen.items(), key=lambda x: x[1])
            drop_count = max(1, len(sorted_items) // 5)
            for k, _ in sorted_items[:drop_count]:
                self._seen.pop(k, None)

        self._seen[message_id] = now
        return False

    def _clean_expired(self, now: float) -> None:
        expired_keys = [k for k, t in self._seen.items() if now - t > self.ttl_seconds]
        for k in expired_keys:
            self._seen.pop(k, None)


class WhatsAppSessionManager:
    """Manages multi-turn conversation session history for WhatsApp users."""

    def __init__(self, max_history_turns: int = 10, session_ttl_seconds: int = 86400):
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self.max_history_turns = max_history_turns
        self.session_ttl_seconds = session_ttl_seconds

    def get_session(self, phone: str) -> Dict[str, Any]:
        clean_phone = "".join(c for c in str(phone or "") if c.isdigit())
        now = time.time()

        session = self._sessions.get(clean_phone)
        if not session or (now - session.get("last_activity", 0) > self.session_ttl_seconds):
            session = {
                "phone": clean_phone,
                "history": [],
                "created_at": now,
                "last_activity": now,
                "agent_type": "Meera (Morning Tax)",
                "customer_name": "",
            }
            self._sessions[clean_phone] = session

        session["last_activity"] = now
        return session

    def add_message(self, phone: str, role: str, content: str, customer_name: Optional[str] = None) -> None:
        session = self.get_session(phone)
        if customer_name and not session.get("customer_name"):
            session["customer_name"] = customer_name

        history: List[Dict[str, str]] = session.get("history", [])
        history.append({"role": role, "content": content})

        # Keep rolling window of history (last N turns = 2*N messages)
        max_messages = self.max_history_turns * 2
        if len(history) > max_messages:
            session["history"] = history[-max_messages:]
        else:
            session["history"] = history

        session["last_activity"] = time.time()

    def get_history(self, phone: str) -> List[Dict[str, str]]:
        session = self.get_session(phone)
        return list(session.get("history", []))

    def clear_session(self, phone: str) -> None:
        clean_phone = "".join(c for c in str(phone or "") if c.isdigit())
        self._sessions.pop(clean_phone, None)


class WhatsAppMessageParser:
    """
    Parses inbound webhooks from Evolution API and standard WhatsApp webhook schemas.
    Extracts sender info, message text, identifiers, and filters non-actionable events.
    """

    @staticmethod
    def parse_webhook_payload(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(payload, dict):
            return None

        event = payload.get("event") or payload.get("type") or ""
        event_lower = str(event).lower()

        # ONLY process messages.upsert. Ignore updates, presence, etc.
        if event_lower and "messages.upsert" not in event_lower:
            return None

        # Robust extraction of the message object from Evolution API payload
        data = payload.get("data")
        msg_obj = None
        
        # Evolution API v2 often wraps the message in a list
        if isinstance(data, list) and len(data) > 0:
            msg_obj = data[0]
        elif isinstance(data, dict):
            # Sometimes data has "messages" array
            if "messages" in data and isinstance(data["messages"], list) and len(data["messages"]) > 0:
                msg_obj = data["messages"][0]
            # Sometimes data itself is the message object (v1)
            elif "key" in data or "message" in data:
                msg_obj = data
            else:
                msg_obj = data
        else:
            msg_obj = payload

        if not isinstance(msg_obj, dict):
            return None

        # Extract 'key' safely
        key = msg_obj.get("key") or {}
        if not key and isinstance(msg_obj.get("message"), dict):
            key = msg_obj["message"].get("key") or {}

        # If we still can't find 'key', we can't reliably determine fromMe or remoteJid
        is_from_me = bool(key.get("fromMe") or msg_obj.get("fromMe") or False)
        if is_from_me:
            # Ignore messages sent by the bot itself to avoid infinite loops
            return None

        remote_jid = key.get("remoteJid") or msg_obj.get("remoteJid") or msg_obj.get("from") or ""
        if not remote_jid:
            return None

        # Ignore group messages and broadcast/status messages
        if "@g.us" in remote_jid or "status@broadcast" in remote_jid:
            return None

        # Extract normalized phone number
        raw_phone = remote_jid.split("@")[0] if "@" in remote_jid else remote_jid
        clean_phone = "".join(c for c in str(raw_phone) if c.isdigit())
        if len(clean_phone) == 10:
            clean_phone = "91" + clean_phone

        if len(clean_phone) < 7:
            return None

        message_id = str(key.get("id") or msg_obj.get("id") or msg_obj.get("messageId") or "")
        push_name = str(msg_obj.get("pushName") or msg_obj.get("senderName") or msg_obj.get("name") or "")

        # Extract message text from various possible Evolution / WhatsApp payload formats
        inner_msg = msg_obj.get("message") or msg_obj
        text = ""

        if isinstance(inner_msg, dict):
            if "conversation" in inner_msg and inner_msg["conversation"]:
                text = str(inner_msg["conversation"])
            elif "extendedTextMessage" in inner_msg and isinstance(inner_msg["extendedTextMessage"], dict):
                text = str(inner_msg["extendedTextMessage"].get("text") or "")
            elif "imageMessage" in inner_msg and isinstance(inner_msg["imageMessage"], dict):
                text = str(inner_msg["imageMessage"].get("caption") or "")
            elif "documentMessage" in inner_msg and isinstance(inner_msg["documentMessage"], dict):
                text = str(inner_msg["documentMessage"].get("caption") or "")
            elif "buttonsResponseMessage" in inner_msg and isinstance(inner_msg["buttonsResponseMessage"], dict):
                text = str(inner_msg["buttonsResponseMessage"].get("selectedDisplayText") or inner_msg["buttonsResponseMessage"].get("selectedButtonId") or "")
            elif "templateButtonReplyMessage" in inner_msg and isinstance(inner_msg["templateButtonReplyMessage"], dict):
                text = str(inner_msg["templateButtonReplyMessage"].get("selectedId") or inner_msg["templateButtonReplyMessage"].get("selectedDisplayText") or "")
            elif "listResponseMessage" in inner_msg and isinstance(inner_msg["listResponseMessage"], dict):
                text = str(inner_msg["listResponseMessage"].get("title") or inner_msg["listResponseMessage"].get("singleSelectReply", {}).get("selectedRowId") or "")

        if not text and isinstance(msg_obj.get("text"), str):
            text = msg_obj["text"]
        elif not text and isinstance(msg_obj.get("body"), str):
            text = msg_obj["body"]

        text = text.strip()
        if not text:
            # Non-text or empty message
            return None

        return {
            "message_id": message_id,
            "sender_phone": clean_phone,
            "sender_name": push_name,
            "remote_jid": remote_jid,
            "text": text,
            "instance_name": payload.get("instance") or EVOLUTION_INSTANCE_NAME or "callinggen",
            "timestamp": msg_obj.get("messageTimestamp") or int(time.time()),
        }


class WhatsAppBotPipeline:
    """
    Core pipeline adapter connecting WhatsApp input to the existing prompt builder and LLM engine.
    Maintains user context, queries existing LLM, and formats responses.
    """

    def __init__(self):
        self.dedup_store = DeduplicationStore()
        self.session_manager = WhatsAppSessionManager()

    def get_system_prompt(self, agent_type: str = "Meera (Morning Tax)", customer_name: str = "") -> str:
        """
        Reuses the agent persona prompt and injects strict context boundaries so the bot
        does not answer out-of-scope or unrelated questions.
        """
        try:
            from agent import AGENT_BASE_PROMPTS
            base_prompt = AGENT_BASE_PROMPTS.get(agent_type) or AGENT_BASE_PROMPTS.get("Meera (Morning Tax)", "")
        except Exception as e:
            print(f"[WhatsAppBotPipeline] Note: Using fallback base prompt: {e}")
            base_prompt = (
                "You are Meera, a friendly and professional tax consultant at Morning Tax. "
                "Your goal is to assist customers with tax filing inquiries, explain deductions and tax planning, "
                "and help schedule a 15-minute consultation with a Senior Tax Strategist."
            )

        if customer_name:
            base_prompt += f"\nThe customer's name is {customer_name}."

        chat_instructions = (
            "\n\nWHATSAPP CHAT INSTRUCTIONS & STRICT BOUNDARIES:\n"
            "- You are communicating directly over WhatsApp chat.\n"
            "- Keep replies concise, helpful, friendly, and natural (1 to 3 short sentences or brief bullet points).\n"
            "- STRICT CONTEXT BOUNDARIES: Only answer questions strictly related to tax consulting, ITR filing, deductions, amended returns, tax planning, appointments, or Morning Tax services.\n"
            "- OUT-OF-SCOPE FALLBACK: If the customer asks an out-of-context question, general trivia, unrelated topic (e.g. personal life, sports, coding, cooking, unrelated products, general chit-chat), or anything not covered in your script/domain, DO NOT answer the question. You MUST reply with:\n"
            "  \"I don't have that info right now. What else can I help you with regarding our tax services?\"\n"
            "- Do not output audio tags or tool calls."
        )

        return base_prompt + chat_instructions

    async def generate_response(self, phone: str, user_message: str, customer_name: str = "", agent_type: str = "Meera (Morning Tax)") -> str:
        """
        Invokes the existing LLM (DeepSeek / OpenAI) with full conversation history and system instructions.
        """
        # Update session with incoming user message
        self.session_manager.add_message(phone, "user", user_message, customer_name=customer_name)
        history = self.session_manager.get_history(phone)

        # Build prompt messages
        system_prompt = self.get_system_prompt(agent_type=agent_type, customer_name=customer_name)
        messages = [{"role": "system", "content": system_prompt}]

        for turn in history:
            messages.append({"role": turn["role"], "content": turn["content"]})

        deepseek_key = os.getenv("DEEPSEEK_API_KEY")
        openai_key = os.getenv("OPENAI_API_KEY")

        from openai import AsyncOpenAI

        # Replicate existing LLM configuration: DeepSeek with OpenAI fallback
        if deepseek_key:
            client = AsyncOpenAI(
                api_key=deepseek_key,
                base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
            )
            model_name = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
        elif openai_key:
            client = AsyncOpenAI(api_key=openai_key)
            model_name = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        else:
            # In mock or test environment without keys
            reply = f"Hello {customer_name or 'there'}! Thank you for reaching out to Morning Tax. How can I assist you with your tax filing today?"
            self.session_manager.add_message(phone, "assistant", reply)
            return reply

        try:
            response = await client.chat.completions.create(
                model=model_name,
                messages=messages,
                temperature=0.3,
                max_tokens=250,
            )
            reply = response.choices[0].message.content or ""
            reply = reply.strip()
        except Exception as err:
            print(f"[WhatsAppBotPipeline] Error invoking LLM: {err}")
            reply = "I apologize, I'm experiencing a brief connection delay. Please feel free to ask your question again or let us know if you'd like to schedule a consultation."

        # Save assistant response to multi-turn session
        self.session_manager.add_message(phone, "assistant", reply)
        return reply

    async def process_incoming_message(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Full orchestration: Parse webhook -> Deduplicate -> Call LLM -> Deliver Response Adapter.
        """
        parsed = WhatsAppMessageParser.parse_webhook_payload(payload)
        if not parsed:
            return {"status": "ignored", "reason": "non_actionable_or_outbound"}

        message_id = parsed["message_id"]
        sender_phone = parsed["sender_phone"]
        sender_name = parsed["sender_name"]
        text = parsed["text"]
        remote_jid = parsed["remote_jid"]
        instance_name = parsed.get("instance_name") or EVOLUTION_INSTANCE_NAME or "callinggen"

        # Deduplication check
        if message_id and self.dedup_store.is_duplicate(message_id):
            print(f"[WhatsAppBotPipeline] Skipping duplicate message_id '{message_id}'")
            return {"status": "skipped_duplicate", "message_id": message_id}

        print(f"[WhatsAppBotPipeline] Inbound WhatsApp message from {sender_phone} ({sender_name}): {text}")

        # Generate response using core LLM and prompts
        response_text = await self.generate_response(
            phone=sender_phone,
            user_message=text,
            customer_name=sender_name,
        )

        # Deliver response back to WhatsApp user
        send_result = None
        try:
            send_result = await evolution_service.send_text_message(
                instance_name=instance_name,
                number=remote_jid,
                text=response_text,
            )
            print(f"[WhatsAppBotPipeline] Sent WhatsApp response to {sender_phone}")
        except Exception as send_err:
            print(f"[WhatsAppBotPipeline] Error dispatching WhatsApp response to {sender_phone}: {send_err}")

        return {
            "status": "success",
            "sender_phone": sender_phone,
            "response": response_text,
            "send_result": send_result,
        }


# Singleton pipeline instance for shared session state
whatsapp_bot_pipeline = WhatsAppBotPipeline()
