import pytest
import time
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import AsyncClient, ASGITransport

from app.main import app
from whatsapp.bot_adapter import (
    WhatsAppMessageParser,
    WhatsAppSessionManager,
    DeduplicationStore,
    WhatsAppBotPipeline,
    whatsapp_bot_pipeline,
)


def test_whatsapp_message_parser_valid_text():
    payload = {
        "event": "messages.upsert",
        "instance": "callinggen_test",
        "data": {
            "key": {
                "remoteJid": "919876543210@s.whatsapp.net",
                "fromMe": False,
                "id": "MSG_12345",
            },
            "pushName": "John Doe",
            "message": {
                "conversation": "Hello, I have a question about my tax filing."
            },
            "messageTimestamp": 1725000000,
        },
    }

    parsed = WhatsAppMessageParser.parse_webhook_payload(payload)
    assert parsed is not None
    assert parsed["message_id"] == "MSG_12345"
    assert parsed["sender_phone"] == "919876543210"
    assert parsed["sender_name"] == "John Doe"
    assert parsed["text"] == "Hello, I have a question about my tax filing."
    assert parsed["instance_name"] == "callinggen_test"


def test_whatsapp_message_parser_extended_text():
    payload = {
        "event": "messages.upsert",
        "data": {
            "key": {
                "remoteJid": "9876543210@s.whatsapp.net",
                "fromMe": False,
                "id": "MSG_EXT_01",
            },
            "pushName": "Jane",
            "message": {
                "extendedTextMessage": {
                    "text": "Can we schedule a call for tomorrow?"
                }
            },
        },
    }

    parsed = WhatsAppMessageParser.parse_webhook_payload(payload)
    assert parsed is not None
    assert parsed["sender_phone"] == "919876543210"
    assert parsed["text"] == "Can we schedule a call for tomorrow?"


def test_whatsapp_message_parser_from_me_ignored():
    payload = {
        "event": "messages.upsert",
        "data": {
            "key": {
                "remoteJid": "919876543210@s.whatsapp.net",
                "fromMe": True,
                "id": "MSG_BOT_OUT",
            },
            "message": {
                "conversation": "I am the bot responding"
            },
        },
    }
    parsed = WhatsAppMessageParser.parse_webhook_payload(payload)
    assert parsed is None


def test_whatsapp_message_parser_group_ignored():
    payload = {
        "event": "messages.upsert",
        "data": {
            "key": {
                "remoteJid": "123456789-987654@g.us",
                "fromMe": False,
                "id": "MSG_GROUP",
            },
            "message": {
                "conversation": "Hello group"
            },
        },
    }
    parsed = WhatsAppMessageParser.parse_webhook_payload(payload)
    assert parsed is None


def test_deduplication_store():
    store = DeduplicationStore(ttl_seconds=60)
    assert store.is_duplicate("MSG_100") is False
    assert store.is_duplicate("MSG_100") is True
    assert store.is_duplicate("MSG_101") is False


def test_session_manager_multi_turn():
    manager = WhatsAppSessionManager(max_history_turns=3)
    phone = "919999988888"

    manager.add_message(phone, "user", "Hi there", customer_name="Alice")
    manager.add_message(phone, "assistant", "Hello Alice! How can I help?")
    manager.add_message(phone, "user", "What are your services?")
    manager.add_message(phone, "assistant", "We provide tax consultation services.")

    history = manager.get_history(phone)
    assert len(history) == 4
    assert history[0] == {"role": "user", "content": "Hi there"}
    assert history[1] == {"role": "assistant", "content": "Hello Alice! How can I help?"}

    session = manager.get_session(phone)
    assert session["customer_name"] == "Alice"

    # Test rolling window truncation
    manager.add_message(phone, "user", "Turn 3")
    manager.add_message(phone, "assistant", "Reply 3")
    manager.add_message(phone, "user", "Turn 4 (overflow)")
    manager.add_message(phone, "assistant", "Reply 4")

    history_after = manager.get_history(phone)
    # Max turns is 3 (so 6 messages max)
    assert len(history_after) == 6
    assert history_after[-1] == {"role": "assistant", "content": "Reply 4"}


@pytest.mark.asyncio
async def test_bot_pipeline_full_flow():
    pipeline = WhatsAppBotPipeline()

    mock_chat_completion = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = "Certainly! We can help with your tax return review."
    mock_chat_completion.choices = [mock_choice]

    mock_openai_client = MagicMock()
    mock_openai_client.chat.completions.create = AsyncMock(return_value=mock_chat_completion)

    payload = {
        "event": "messages.upsert",
        "instance": "callinggen_test",
        "data": {
            "key": {
                "remoteJid": "919123456780@s.whatsapp.net",
                "fromMe": False,
                "id": "MSG_E2E_01",
            },
            "pushName": "Sam",
            "message": {
                "conversation": "Can you review my tax return?"
            },
        },
    }

    with patch("openai.AsyncOpenAI", return_value=mock_openai_client), \
         patch("whatsapp.service.send_text_message", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = {"status": "SUCCESS"}

        result = await pipeline.process_incoming_message(payload)

        assert result["status"] == "success"
        assert result["sender_phone"] == "919123456780"
        assert "tax return review" in result["response"]
        assert mock_send.call_count == 1
        mock_send.assert_called_once_with(
            instance_name="callinggen_test",
            number="919123456780",
            text="Certainly! We can help with your tax return review.",
        )


@pytest.mark.asyncio
async def test_webhook_endpoints_integration():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. GET /api/whatsapp/webhook verification
        res_get = await client.get("/api/whatsapp/webhook")
        assert res_get.status_code == 200
        assert res_get.json()["status"] == "active"

        # 2. POST /api/whatsapp/webhook event
        webhook_payload = {
            "event": "messages.upsert",
            "instance": "callinggen",
            "data": {
                "key": {
                    "remoteJid": "919876500000@s.whatsapp.net",
                    "fromMe": False,
                    "id": "MSG_HTTP_001",
                },
                "pushName": "Chris",
                "message": {
                    "conversation": "Hello from WhatsApp!"
                },
            },
        }

        with patch("whatsapp.service.send_text_message", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = {"status": "SUCCESS"}
            res_post = await client.post("/api/whatsapp/webhook", json=webhook_payload)
            assert res_post.status_code == 200
            data = res_post.json()
            assert data["success"] is True
            assert data["data"]["status"] == "success"
            assert data["data"]["sender_phone"] == "919876500000"

        # 3. POST /api/whatsapp/bot/interact
        interact_payload = {
            "phone": "919876500000",
            "message": "Do you offer cross-border tax advice?",
            "customer_name": "Chris",
        }
        res_interact = await client.post("/api/whatsapp/bot/interact", json=interact_payload)
        assert res_interact.status_code == 200
        interact_data = res_interact.json()
        assert interact_data["success"] is True
        assert len(interact_data["reply"]) > 0
