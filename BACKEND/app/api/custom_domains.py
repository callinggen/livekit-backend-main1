from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
import resend

from app.database import get_db
from app.models.user import User
from app.models.custom_domain import CustomEmailDomain
from app.schemas.custom_domain import (
    CustomDomainCreate,
    CustomDomainOut,
    DnsVerificationResponse,
    VerifiedSenderOut,
)
from app.services.custom_domain_service import CustomDomainService
from app.services.email_service import email_service
from app.api.auth import get_current_user


router = APIRouter(prefix="/custom-domains", tags=["Custom Sending Domains"])


@router.get("", response_model=List[CustomDomainOut])
async def list_custom_domains(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all custom sending domains for the authenticated user."""
    stmt = (
        select(CustomEmailDomain)
        .where(CustomEmailDomain.user_id == current_user.id)
        .order_by(CustomEmailDomain.created_at.desc())
    )
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("", response_model=CustomDomainOut, status_code=status.HTTP_201_CREATED)
async def create_custom_domain(
    payload: CustomDomainCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Register a new custom sending domain.
    Requests required DNS configuration from Resend and stores it in CallingGen.
    """
    try:
        domain_obj = await CustomDomainService.create_domain(
            db=db,
            user_id=current_user.id,
            raw_domain=payload.domain,
            region=payload.region or "us-east-1",
        )
        return domain_obj
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error registering custom domain: {str(e)}",
        )


@router.get("/verified-senders", response_model=List[VerifiedSenderOut])
async def get_verified_senders(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Returns all authorized and verified sender domains for Email Marketing.
    Always includes the default platform sender plus any user-verified custom domains.
    """
    default_from = email_service.from_email
    clean_default_email = (
        default_from.split("<")[1].split(">")[0].strip()
        if "<" in default_from and ">" in default_from
        else default_from.strip()
    )

    senders: List[VerifiedSenderOut] = [
        VerifiedSenderOut(
            email=clean_default_email,
            display_name=current_user.company_name or "CallingGen",
            domain="callinggen.in",
            is_default=True,
            is_verified=True,
        )
    ]

    # Fetch user's verified custom domains
    stmt = select(CustomEmailDomain).where(
        and_(
            CustomEmailDomain.user_id == current_user.id,
            CustomEmailDomain.is_verified == True,
        )
    )
    result = await db.execute(stmt)
    verified_domains = result.scalars().all()

    for d in verified_domains:
        senders.append(
            VerifiedSenderOut(
                email=f"info@{d.domain}",
                display_name=current_user.company_name or d.domain,
                domain=d.domain,
                is_default=False,
                is_verified=True,
            )
        )

    return senders


@router.get("/{domain_id}", response_model=CustomDomainOut)
async def get_custom_domain(
    domain_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get single custom domain details with latest DNS configuration."""
    stmt = select(CustomEmailDomain).where(
        and_(
            CustomEmailDomain.id == domain_id,
            CustomEmailDomain.user_id == current_user.id,
        )
    )
    domain_obj = (await db.execute(stmt)).scalars().first()
    if not domain_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Custom domain not found or access denied.",
        )
    return domain_obj


@router.post("/{domain_id}/verify", response_model=DnsVerificationResponse)
async def verify_custom_domain(
    domain_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Performs server-side public DNS lookup for all required records and synchronizes with Resend.
    """
    stmt = select(CustomEmailDomain).where(
        and_(
            CustomEmailDomain.id == domain_id,
            CustomEmailDomain.user_id == current_user.id,
        )
    )
    domain_obj = (await db.execute(stmt)).scalars().first()
    if not domain_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Custom domain not found or access denied.",
        )

    try:
        updated = await CustomDomainService.verify_domain(db=db, domain_obj=domain_obj)
        all_matched = all(
            bool(r.get("dns_verified")) for r in (updated.dns_records or [])
        )
        
        msg = (
            f"Domain '{updated.domain}' is fully verified and ready for sending!"
            if updated.is_verified
            else f"DNS check completed. Some records are still pending propagation."
        )

        return DnsVerificationResponse(
            domain_id=updated.id,
            domain=updated.domain,
            status=updated.status,
            is_verified=updated.is_verified,
            sending_enabled=updated.sending_enabled,
            all_dns_matched=all_matched,
            dns_records=updated.dns_records or [],
            message=msg,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"DNS verification error: {str(e)}",
        )


@router.delete("/{domain_id}")
async def delete_custom_domain(
    domain_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete custom domain from CallingGen and remove from Resend."""
    stmt = select(CustomEmailDomain).where(
        and_(
            CustomEmailDomain.id == domain_id,
            CustomEmailDomain.user_id == current_user.id,
        )
    )
    domain_obj = (await db.execute(stmt)).scalars().first()
    if not domain_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Custom domain not found or access denied.",
        )

    # Attempt Resend deletion if connected
    if (
        email_service.is_configured()
        and domain_obj.resend_domain_id
        and not domain_obj.resend_domain_id.startswith("mock_")
        and not domain_obj.resend_domain_id.startswith("restricted_")
    ):
        try:
            resend.api_key = email_service.api_key
            resend.Domains.remove(domain_obj.resend_domain_id)
        except Exception as e:
            print(f"[CustomDomainRouter] Resend delete note: {e}")

    await db.delete(domain_obj)
    await db.commit()
    return {"message": f"Domain '{domain_obj.domain}' has been successfully deleted."}
