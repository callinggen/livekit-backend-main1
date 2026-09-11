from typing import List
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, and_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.models.user_smtp_config import UserSmtpConfig
from app.schemas.smtp_config import (
    SmtpMailboxCreate,
    SmtpMailboxOut,
    SmtpMailboxTest,
    SmtpTestResult,
)
from app.services.smtp_mailbox_service import SmtpMailboxService, PROVIDER_PRESETS
from app.core.crypto import encrypt_secret
from app.api.auth import get_current_user

router = APIRouter(prefix="/smtp-mailboxes", tags=["Connected Mailboxes (SMTP)"])


@router.get("/presets")
async def get_provider_presets():
    """Get preset server configurations for popular providers."""
    return PROVIDER_PRESETS


@router.get("", response_model=List[SmtpMailboxOut])
async def list_mailboxes(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all connected SMTP email accounts for the current user."""
    stmt = (
        select(UserSmtpConfig)
        .where(UserSmtpConfig.user_id == current_user.id)
        .order_by(UserSmtpConfig.is_default.desc(), UserSmtpConfig.created_at.desc())
    )
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("/test", response_model=SmtpTestResult)
async def test_smtp_connection(
    payload: SmtpMailboxTest,
    current_user: User = Depends(get_current_user),
):
    """
    Test SMTP connection and send a test verification email without saving.
    """
    res = await SmtpMailboxService.test_credentials(
        host=payload.smtp_host.strip(),
        port=payload.smtp_port,
        encryption=payload.smtp_encryption,
        username=payload.username.strip(),
        password=payload.password.strip(),
        sender_name=payload.sender_name.strip(),
        sender_email=payload.sender_email.strip(),
        recipient_email=payload.recipient_email or payload.sender_email.strip(),
    )
    if not res["success"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=res["message"],
        )
    return SmtpTestResult(
        success=True,
        message=res["message"],
        tested_at=datetime.now(timezone.utc),
    )


@router.post("", response_model=SmtpMailboxOut, status_code=status.HTTP_201_CREATED)
async def create_mailbox(
    payload: SmtpMailboxCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Connect and save a new SMTP mailbox.
    Optionally validates connection and sends a verification email.
    """
    clean_email = payload.sender_email.strip().lower()

    # Check if this email is already connected for this user
    stmt = select(UserSmtpConfig).where(
        and_(
            UserSmtpConfig.user_id == current_user.id,
            UserSmtpConfig.sender_email.ilike(clean_email),
        )
    )
    existing = (await db.execute(stmt)).scalars().first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Mailbox '{clean_email}' is already connected to your account. You can re-test or edit it.",
        )

    is_verified = False
    error_msg = None

    if payload.send_test_on_create:
        test_res = await SmtpMailboxService.test_credentials(
            host=payload.smtp_host.strip(),
            port=payload.smtp_port,
            encryption=payload.smtp_encryption,
            username=payload.username.strip(),
            password=payload.password.strip(),
            sender_name=payload.sender_name.strip(),
            sender_email=clean_email,
        )
        if not test_res["success"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Connection test failed: {test_res['message']}",
            )
        is_verified = True

    # If setting as default, unset other defaults
    if payload.is_default:
        await db.execute(
            update(UserSmtpConfig)
            .where(UserSmtpConfig.user_id == current_user.id)
            .values(is_default=False)
        )

    # Check if this is the user's first mailbox; if so, make default
    count_stmt = select(UserSmtpConfig).where(UserSmtpConfig.user_id == current_user.id)
    count = len((await db.execute(count_stmt)).scalars().all())
    set_default = payload.is_default or (count == 0)

    encrypted_pwd = encrypt_secret(payload.password.strip())

    mailbox = UserSmtpConfig(
        user_id=current_user.id,
        provider=payload.provider.lower(),
        sender_name=payload.sender_name.strip(),
        sender_email=clean_email,
        smtp_host=payload.smtp_host.strip(),
        smtp_port=payload.smtp_port,
        smtp_encryption=payload.smtp_encryption.lower(),
        username=payload.username.strip(),
        encrypted_password=encrypted_pwd,
        is_verified=is_verified,
        is_active=True,
        is_default=set_default,
        last_tested_at=datetime.now(timezone.utc).replace(tzinfo=None) if is_verified else None,
        error_message=error_msg,
    )
    db.add(mailbox)
    await db.commit()
    await db.refresh(mailbox)
    return mailbox


@router.post("/{mailbox_id}/test", response_model=SmtpTestResult)
async def test_existing_mailbox(
    mailbox_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Re-test an existing connected mailbox by ID."""
    mailbox = await db.get(UserSmtpConfig, mailbox_id)
    if not mailbox or mailbox.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mailbox not found",
        )

    from app.core.crypto import decrypt_secret
    pwd = decrypt_secret(mailbox.encrypted_password)

    res = await SmtpMailboxService.test_credentials(
        host=mailbox.smtp_host,
        port=mailbox.smtp_port,
        encryption=mailbox.smtp_encryption,
        username=mailbox.username,
        password=pwd,
        sender_name=mailbox.sender_name,
        sender_email=mailbox.sender_email,
    )

    mailbox.last_tested_at = datetime.now(timezone.utc).replace(tzinfo=None)
    mailbox.is_verified = res["success"]
    mailbox.error_message = None if res["success"] else res["message"][:500]
    await db.commit()

    if not res["success"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=res["message"],
        )

    return SmtpTestResult(
        success=True,
        message=res["message"],
        tested_at=datetime.now(timezone.utc),
    )


@router.post("/{mailbox_id}/set-default")
async def set_default_mailbox(
    mailbox_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Set a mailbox as the default sending account."""
    mailbox = await db.get(UserSmtpConfig, mailbox_id)
    if not mailbox or mailbox.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Mailbox not found")

    await db.execute(
        update(UserSmtpConfig)
        .where(UserSmtpConfig.user_id == current_user.id)
        .values(is_default=False)
    )
    mailbox.is_default = True
    await db.commit()
    return {"message": f"'{mailbox.sender_email}' is now your default mailbox"}


@router.delete("/{mailbox_id}")
async def delete_mailbox(
    mailbox_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Disconnect and remove an SMTP mailbox."""
    mailbox = await db.get(UserSmtpConfig, mailbox_id)
    if not mailbox or mailbox.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Mailbox not found")

    await db.delete(mailbox)
    await db.commit()
    return {"message": "Mailbox disconnected successfully"}
