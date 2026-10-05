import os
from dotenv import load_dotenv
load_dotenv()

# Optional Enterprise APM Integration (Sentry)
SENTRY_DSN = os.getenv("SENTRY_DSN")
if SENTRY_DSN:
    try:
        import sentry_sdk  # type: ignore # pyright: ignore[reportMissingImports]
        from sentry_sdk.integrations.fastapi import FastApiIntegration  # type: ignore # pyright: ignore[reportMissingImports]
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration  # type: ignore # pyright: ignore[reportMissingImports]
        sentry_sdk.init(
            dsn=SENTRY_DSN,
            environment=os.getenv("ENVIRONMENT", "staging"),
            traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.2")),
            integrations=[FastApiIntegration(), SqlalchemyIntegration()],
        )
        print(f"[APM] Sentry initialized in environment: {os.getenv('ENVIRONMENT', 'staging')}")
    except Exception as e:
        print(f"[APM] Could not initialize Sentry: {e}")


import re
import jwt
from typing import Optional

import logging
from fastapi import FastAPI, HTTPException, Header, Query, Request
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.middleware import (
    RequestIdAndTracingMiddleware,
    SecurityHeadersMiddleware,
    RateLimitMiddleware,
)
from app.api.campaigns import router as campaign_router
from app.api.calls import router as call_router
from app.api.auth import router as auth_router
from app.api.admin import router as admin_router
from app.api.reports import router as report_router
from app.api.agents import router as agents_router
from app.api.demo import router as demo_router
from app.api.calendar import router as calendar_router
from app.api.email_campaigns import router as email_campaign_router
from app.api.email_templates import router as email_template_router
from app.api.custom_domains import router as custom_domain_router
from app.api.smtp_configs import router as smtp_configs_router
from app.api.phone_numbers import router as phone_numbers_router
from app.api.payments import router as payment_router
from app.api.whatsapp_send import router as whatsapp_send_router
from app.api.whatsapp_materials import router as whatsapp_materials_router
from app.api.whatsapp_history import router as whatsapp_history_router
from app.api.contacts_book import router as contacts_book_router
from app.api.billing import router as billing_router
from app.api.knowledge import router as knowledge_router
from whatsapp.routes import router as whatsapp_router


# Ensure recordings directory exists
os.makedirs("recordings", exist_ok=True)

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

SCHEDULER_POLL_INTERVAL = int(os.getenv("SCHEDULER_POLL_INTERVAL", "15"))

from app.database import AsyncSessionLocal, Base, engine
from sqlalchemy import select
from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.job import Job
from app.models.call import Call
from app.models.user import User
from app.models.password_reset import PasswordReset
from app.models.notification_state import UserNotificationState
from app.models.email_campaign import EmailCampaign  # registers email tables
from app.models.email_contact import EmailContact    # registers email tables
from app.models.email_template import EmailMarketingTemplate  # registers template table
from app.models.custom_domain import CustomEmailDomain        # registers custom domain table
from app.models.user_smtp_config import UserSmtpConfig        # registers smtp mailbox table
from app.models.payment import Payment
from app.models.whatsapp_action import WhatsAppAction
from app.models.whatsapp_material import WhatsAppMaterial
from app.models.whatsapp_send_job import WhatsAppSendJob
from app.models.whatsapp_send_recipient import WhatsAppSendRecipient
from app.models.saved_contact import SavedContact
from app.models.knowledge import KnowledgeDocument, KnowledgeChunk  # registers knowledge tables

from app.core.security import get_password_hash
from app.services.campaign_service import CampaignService

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure all tables exist on startup
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        from sqlalchemy import text
        for col_name in ["company_name", "industry", "agent_name", "agent_language", "agent_voice", "agent_script", "is_active"]:
            try:
                await conn.execute(text(f"ALTER TABLE users ADD COLUMN {col_name} VARCHAR;"))
            except Exception:
                pass
                
        for col_name in ["campaign_type", "parent_campaign_id", "pre_start_notified", "start_notified", "completed_notified"]:
            try:
                if col_name == "campaign_type":
                    await conn.execute(text("ALTER TABLE campaigns ADD COLUMN campaign_type VARCHAR DEFAULT 'normal';"))
                elif col_name == "parent_campaign_id":
                    await conn.execute(text("ALTER TABLE campaigns ADD COLUMN parent_campaign_id INTEGER;"))
                elif col_name in ["pre_start_notified", "start_notified", "completed_notified"]:
                    await conn.execute(text(f"ALTER TABLE campaigns ADD COLUMN {col_name} BOOLEAN DEFAULT 0;"))
            except Exception:
                pass

        for col_name in ["upload_source", "sheet_name"]:
            try:
                await conn.execute(text(f"ALTER TABLE campaigns ADD COLUMN {col_name} VARCHAR;"))
            except Exception:
                pass
                
        try:
            await conn.execute(text("ALTER TABLE campaigns ADD COLUMN outbound_phone_number VARCHAR;"))
        except Exception:
            pass

        # Per-line concurrency support
        try:
            await conn.execute(text("ALTER TABLE user_phone_numbers ADD COLUMN max_concurrent_calls INTEGER DEFAULT 3;"))
        except Exception:
            pass

        try:
            await conn.execute(text("ALTER TABLE contacts ADD COLUMN original_row INTEGER;"))
        except Exception:
            pass

        try:
            await conn.execute(text("ALTER TABLE whatsapp_send_jobs ADD COLUMN scheduled_for TIMESTAMP;"))
        except Exception:
            pass

        # Inbound columns migrations
        for col_name in ["direction", "caller_number", "called_number"]:
            try:
                await conn.execute(text(f"ALTER TABLE calls ADD COLUMN {col_name} VARCHAR;"))
            except Exception:
                pass
        for col_name in ["phone_line_id", "tenant_id", "agent_id"]:
            try:
                await conn.execute(text(f"ALTER TABLE calls ADD COLUMN {col_name} INTEGER;"))
            except Exception:
                pass
        # Set default direction for existing calls
        try:
            await conn.execute(text("UPDATE calls SET direction = 'outbound' WHERE direction IS NULL;"))
        except Exception:
            pass

        try:
            await conn.execute(text("ALTER TABLE user_phone_numbers ADD COLUMN inbound_enabled BOOLEAN DEFAULT 0;"))
        except Exception:
            pass
        try:
            await conn.execute(text("ALTER TABLE user_phone_numbers ADD COLUMN inbound_agent_id INTEGER;"))
        except Exception:
            pass

        # Call lifecycle, SIP state, and billing auto-migrations
        for col_name, col_type in [
            ("outcome", "VARCHAR"),
            ("failure_reason", "VARCHAR"),
            ("sip_was_active", "BOOLEAN DEFAULT 0"),
            ("answered_at", "DATETIME"),
            ("billing_status", "VARCHAR DEFAULT 'pending'"),
        ]:
            try:
                await conn.execute(text(f"ALTER TABLE calls ADD COLUMN {col_name} {col_type};"))
            except Exception:
                pass



        # Email Campaign columns auto-migrations
        for col_name, col_type in [
            ("from_name", "VARCHAR"),
            ("from_email", "VARCHAR"),
            ("reply_to", "VARCHAR"),
            ("schedule_date", "VARCHAR"),
            ("schedule_time", "VARCHAR"),
            ("total_sent", "INTEGER DEFAULT 0"),
            ("total_failed", "INTEGER DEFAULT 0"),
        ]:
            try:
                await conn.execute(text(f"ALTER TABLE email_campaigns ADD COLUMN {col_name} {col_type};"))
            except Exception:
                pass

        # Performance index auto-migrations
        for idx_sql in [
            "CREATE INDEX IF NOT EXISTS ix_calls_campaign_id ON calls(campaign_id);",
            "CREATE INDEX IF NOT EXISTS ix_calls_room_name ON calls(room_name);",
            "CREATE INDEX IF NOT EXISTS ix_calls_status ON calls(status);",
            "CREATE INDEX IF NOT EXISTS ix_calls_started_at ON calls(started_at);",
            "CREATE INDEX IF NOT EXISTS ix_calls_contact_id ON calls(contact_id);",
            "CREATE INDEX IF NOT EXISTS ix_calls_tenant_id ON calls(tenant_id);",
            "CREATE INDEX IF NOT EXISTS ix_calls_phone ON calls(phone);",
            "CREATE INDEX IF NOT EXISTS ix_jobs_campaign_id ON jobs(campaign_id);",
            "CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs(status);",
            "CREATE INDEX IF NOT EXISTS ix_contacts_campaign_id ON contacts(campaign_id);",
            "CREATE INDEX IF NOT EXISTS ix_contacts_status ON contacts(status);",
            "CREATE INDEX IF NOT EXISTS ix_contacts_phone ON contacts(phone);",
            "CREATE INDEX IF NOT EXISTS ix_campaigns_user_id ON campaigns(user_id);",
            "CREATE INDEX IF NOT EXISTS ix_campaigns_status ON campaigns(status);",
        ]:
            try:
                await conn.execute(text(idx_sql))
            except Exception:
                pass

    # Ensure default admin user exists
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(User).where(User.email == "admin@example.com"))
        if not res.scalars().first():
            admin_user = User(
                email="admin@example.com",
                full_name="Admin User",
                hashed_password=get_password_hash("password123!"),
                is_admin=True,
                is_first_login=False,
            )
            db.add(admin_user)
            await db.commit()

        # Seed default marketing email templates if not already present
        try:
            from app.api.email_templates import ensure_default_templates_seeded
            await ensure_default_templates_seeded(db)
        except Exception as seed_err:
            print(f"[STARTUP] Could not seed default email templates: {seed_err}")

    # Startup: Background campaign scheduler is now decoupled into the Worker tier (Tier 4).
    # The API tier remains purely stateless. If ENABLE_SCHEDULER=true is explicitly forced,
    # it can run locally for single-process development.
    enable_sched = os.getenv("ENABLE_SCHEDULER", "false").lower() in ("true", "1", "yes")
    task = None
    if enable_sched:
        from app.services.scheduler_service import SchedulerService
        print("[STARTUP] WARNING: ENABLE_SCHEDULER=true. Running SchedulerService loop inside API instance.")
        task = asyncio.create_task(SchedulerService.run_scheduler_loop())
    else:
        print("[STARTUP] API Tier is purely STATELESS (ENABLE_SCHEDULER=false). Background scheduling is decoupled to the worker tier.")
    yield
    # Shutdown: Cancel scheduler if active
    if task:
        task.cancel()



app = FastAPI(
    title="Calling Platform API",
    version="1.0.0",
    lifespan=lifespan,
)

allowed_origins_env = os.getenv("ALLOWED_ORIGINS")
if allowed_origins_env:
    allowed_origins = [o.strip() for o in allowed_origins_env.split(",") if o.strip()]
else:
    allowed_origins = ["*"]

# Enterprise Security, Tracing, and Throttling Middlewares
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestIdAndTracingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

api_logger = logging.getLogger("callinggen.api")

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    req_id = getattr(request.state, "request_id", "req_unknown")
    api_logger.error(f"[GLOBAL EXCEPTION] [{req_id}] {request.method} {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "An internal server error occurred.",
            "request_id": req_id,
        },
        headers={"X-Request-ID": req_id}
    )

RECORDING_FILENAME_REGEX = re.compile(r"^[a-zA-Z0-9_\-\.]+\.(wav|mp3|ogg)$")

@app.get("/api/recordings/{filename}", tags=["Recordings"])
async def get_recording(
    filename: str,
    token: Optional[str] = Query(None),
    x_internal_secret: Optional[str] = Header(None, alias="X-Internal-Secret")
):
    """
    Secure call audio recording delivery.
    Enforces path traversal protection and optional token-based authorization.
    """
    # 1. Path traversal defense
    if not RECORDING_FILENAME_REGEX.match(filename) or ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid recording filename")

    # 2. Token / internal secret verification when strict auth is enabled
    if os.getenv("REQUIRE_RECORDING_AUTH", "false").lower() in ("true", "1"):
        authorized = False
        internal_secret = os.getenv("INTERNAL_API_SECRET")
        if internal_secret and x_internal_secret == internal_secret:
            authorized = True
        elif token:
            try:
                from app.core.security import SECRET_KEY, ALGORITHM
                jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
                authorized = True
            except Exception:
                pass

        if not authorized:
            raise HTTPException(status_code=401, detail="Unauthorized to access call recording")

    # 3. Decoupled Tier 5 Storage: Redirect to S3 presigned URL if cloud storage enabled
    from app.services.storage_service import StorageService
    s3_url = StorageService.get_presigned_url(filename)
    if s3_url:
        return RedirectResponse(url=s3_url, status_code=307)

    # 4. Fallback to local disk storage
    file_path = os.path.join("recordings", filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Recording not found")

    return FileResponse(file_path, media_type="audio/wav")

app.include_router(call_router, prefix="/api", tags=["Calls"])
app.include_router(report_router, prefix="/api")
app.include_router(demo_router, prefix="/api")
app.include_router(calendar_router, tags=["Calendar"])
app.include_router(campaign_router, prefix="/api", tags=["Campaigns"])
app.include_router(email_campaign_router, prefix="/api", tags=["Email Campaigns"])
app.include_router(email_template_router, prefix="/api", tags=["Email Templates"])
app.include_router(custom_domain_router, prefix="/api", tags=["Custom Sending Domains"])
app.include_router(smtp_configs_router, prefix="/api", tags=["Connected Mailboxes (SMTP)"])
app.include_router(auth_router, prefix="/api/auth", tags=["Auth"])
app.include_router(admin_router, prefix="/api/admin", tags=["Admin"])
app.include_router(agents_router, prefix="/api/agents", tags=["Agents"])
app.include_router(demo_router, prefix="/api/demo", tags=["Demo"])
app.include_router(phone_numbers_router)
app.include_router(payment_router, prefix="/api")
app.include_router(whatsapp_router, prefix="/api/whatsapp", tags=["WhatsApp"])
app.include_router(whatsapp_send_router, prefix="/api/whatsapp", tags=["WhatsApp Send"])
app.include_router(whatsapp_materials_router, prefix="/api/whatsapp", tags=["WhatsApp Materials"])
app.include_router(whatsapp_history_router, prefix="/api/whatsapp", tags=["WhatsApp History"])
app.include_router(contacts_book_router, prefix="/api", tags=["Contacts Book"])
app.include_router(billing_router, prefix="/api", tags=["Universal Billing & Credits"])
app.include_router(knowledge_router, prefix="/api", tags=["Knowledge Base"])


@app.get("/api/health", tags=["Health"])
async def health_check():
    """
    Comprehensive platform health check for Application Load Balancers and uptime monitors.
    Validates PostgreSQL database connectivity and host resource availability.
    """
    db_status = "connected"
    db_error = None
    try:
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
    except Exception as e:
        db_status = "disconnected"
        db_error = str(e)

    memory_info = {}
    try:
        import psutil  # type: ignore
        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()
        memory_info = {
            "ram_used_percent": mem.percent,
            "swap_used_percent": swap.percent,
            "swap_active": swap.total > 0
        }
    except Exception:
        pass

    is_healthy = db_status == "connected"
    response_payload = {
        "status": "healthy" if is_healthy else "degraded",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "database": {
            "status": db_status,
            "error": db_error
        },
        "system": memory_info,
        "version": "1.0.0"
    }

    if not is_healthy:
        return JSONResponse(status_code=503, content=response_payload)
    return response_payload


@app.get("/")
def home():
    return {
        "status": "running",
        "message": "Backend is working",
    }