import os
import uuid
import hmac
import hashlib
from typing import Optional
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models.user import User
from app.core.security import get_current_user
from app.services.universal_credit_service import UniversalCreditService
from app.models.credit_models import CreditTransaction, AdminRateConfig

router = APIRouter(prefix="/billing", tags=["Universal Billing & Credits"])


def _iso_utc(dt: Optional[datetime]) -> Optional[str]:
    """Serialize a datetime as an explicit-UTC ISO string (naive values are treated as UTC)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")

# ── Top-Up Pricing Packages (Page 8 Specification) ───────────────────────────
TOPUP_PACKAGES = {
    100: {"credits": 100, "price_inr": 199, "effective_price": 1.99},
    500: {"credits": 500, "price_inr": 899, "effective_price": 1.80},
    1000: {"credits": 1000, "price_inr": 1699, "effective_price": 1.70},
    2500: {"credits": 2500, "price_inr": 3999, "effective_price": 1.60},
    5000: {"credits": 5000, "price_inr": 7499, "effective_price": 1.50},
    10000: {"credits": 10000, "price_inr": 13999, "effective_price": 1.40},
}

SUBSCRIPTION_PLANS = [
    {
        "id": "starter",
        "name": "Starter",
        "price_inr": 2999,
        "credits": 2000,
        "effective_price": 1.50,
        "voice_agents": "1 AI Voice Agent",
        "automation": "Basic Automation",
        "max_equivalent": {
            "calling_minutes": 133,
            "whatsapp_messages": 20000,
            "emails": 10000,
        },
        "features": [
            "2,000 CallingGen Credits",
            "AI Calling + WhatsApp + Email",
            "~133 Minutes AI Calling OR",
            "20,000 WhatsApp Messages OR",
            "10,000 Emails",
            "1 AI Voice Agent",
            "Basic Automation & Webhook Triggers",
            "Standard Analytics Dashboard",
        ]
    },
    {
        "id": "growth",
        "name": "Growth",
        "price_inr": 6999,
        "credits": 5000,
        "effective_price": 1.40,
        "voice_agents": "3 AI Voice Agents",
        "automation": "Standard Automation",
        "popular": True,
        "max_equivalent": {
            "calling_minutes": 333,
            "whatsapp_messages": 50000,
            "emails": 25000,
        },
        "features": [
            "5,000 CallingGen Credits",
            "AI Calling + WhatsApp + Email",
            "~333 Minutes AI Calling OR",
            "50,000 WhatsApp Messages OR",
            "25,000 Emails",
            "3 AI Voice Agents",
            "Standard Automation & Multi-Channel Workflows",
            "Priority Support & Full Analytics",
        ]
    },
    {
        "id": "pro",
        "name": "Pro",
        "price_inr": 12999,
        "credits": 10000,
        "effective_price": 1.30,
        "voice_agents": "10 AI Voice Agents",
        "automation": "Advanced Automation",
        "max_equivalent": {
            "calling_minutes": 667,
            "whatsapp_messages": 100000,
            "emails": 50000,
        },
        "features": [
            "10,000 CallingGen Credits",
            "AI Calling + WhatsApp + Email",
            "~667 Minutes AI Calling OR",
            "100,000 WhatsApp Messages OR",
            "50,000 Emails",
            "10 AI Voice Agents",
            "Advanced Automation & Workflows",
            "CRM Integrations & API / Webhooks",
            "High Concurrency Telephony",
        ]
    },
    {
        "id": "business",
        "name": "Business",
        "price_inr": 29999,
        "credits": 25000,
        "effective_price": 1.20,
        "voice_agents": "Unlimited AI Voice Agents",
        "automation": "Enterprise Automation",
        "max_equivalent": {
            "calling_minutes": 1667,
            "whatsapp_messages": 250000,
            "emails": 125000,
        },
        "features": [
            "25,000 CallingGen Credits",
            "AI Calling + WhatsApp + Email",
            "~1,667 Minutes AI Calling OR",
            "250,000 WhatsApp Messages OR",
            "125,000 Emails",
            "Unlimited AI Voice Agents",
            "Advanced Automation & Custom Integrations",
            "Custom Concurrency & SIP Integration",
            "Dedicated Infrastructure & Account Manager",
        ]
    },
]


# ── Schemas ──────────────────────────────────────────────────────────────────

class TopupOrderRequest(BaseModel):
    credits: int = Field(..., description="Credits amount (100, 500, 1000, 2500, 5000, 10000)")

class TopupVerifyRequest(BaseModel):
    credits: int
    razorpay_order_id: Optional[str] = None
    razorpay_payment_id: Optional[str] = None
    razorpay_signature: Optional[str] = None

class AdminRateUpdateRequest(BaseModel):
    key: str
    value: float
    description: Optional[str] = ""

class AdminCreditAdjustRequest(BaseModel):
    user_id: int
    amount: float
    type: str = "manual_adjustment" # manual_adjustment, refund, bonus_credit
    description: str


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/wallet")
async def get_wallet(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get Universal Credit Wallet details for the current authenticated user.
    """
    return await UniversalCreditService.get_user_wallet(db, current_user.id)


@router.get("/usage-summary")
async def get_usage_summary(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get aggregated usage across AI Calling, WhatsApp, and Email.
    """
    wallet = await UniversalCreditService.get_user_wallet(db, current_user.id)
    usage = await UniversalCreditService.get_usage_summary(db, current_user.id)
    return {
        "wallet": wallet,
        "usage": usage,
    }


@router.get("/transactions")
async def get_transactions(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get credit transaction ledger history.
    """
    txs = await UniversalCreditService.get_transactions(db, current_user.id, limit=limit, offset=offset)
    return {
        "total": len(txs),
        "limit": limit,
        "offset": offset,
        "transactions": [
            {
                "id": t.id,
                "transaction_id": t.transaction_id,
                "type": t.type,
                "service": t.service,
                "credits": t.credits,
                "reference_id": t.reference_id,
                "description": t.description,
                "balance_before": t.balance_before,
                "balance_after": t.balance_after,
                "rate_version": t.rate_version,
                "created_at": _iso_utc(t.created_at),
            }
            for t in txs
        ]
    }


@router.get("/rates")
async def get_rates(db: AsyncSession = Depends(get_db)):
    """
    Get current active credit conversion rates.
    """
    return await UniversalCreditService.get_rates(db)


@router.get("/plans")
async def get_plans():
    """
    Get CallingGen subscription plans and top-up packages.
    """
    return {
        "plans": SUBSCRIPTION_PLANS,
        "topup_packages": list(TOPUP_PACKAGES.values()),
    }


def calculate_topup_price(credits: int) -> int:
    """Calculate tiered pricing for custom credit slider amounts."""
    if credits in TOPUP_PACKAGES:
        return int(TOPUP_PACKAGES[credits]["price_inr"])
    if credits <= 500:
        rate = 1.80
    elif credits <= 1000:
        rate = 1.70
    elif credits <= 2500:
        rate = 1.60
    elif credits <= 5000:
        rate = 1.50
    else:
        rate = 1.40
    return max(99, round(credits * rate))


@router.post("/topup/create-order")
async def create_topup_order(
    payload: TopupOrderRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Create a Razorpay order for purchasing top-up credits (pre-set package or custom slider).
    """
    if payload.credits < 50:
        raise HTTPException(
            status_code=400,
            detail="Minimum top-up is 50 credits."
        )

    price_inr = calculate_topup_price(payload.credits)
    amount_paisa = price_inr * 100

    # Initialize Razorpay Client if credentials exist
    key_id = os.getenv("RAZORPAY_KEY_ID")
    key_secret = os.getenv("RAZORPAY_KEY_SECRET")

    order_id = f"order_mock_{uuid.uuid4().hex[:14]}"
    if key_id and key_secret:
        try:
            import razorpay  # type: ignore[import-not-found]
            client = razorpay.Client(auth=(key_id, key_secret))
            rz_order = client.order.create({
                "amount": amount_paisa,
                "currency": "INR",
                "payment_capture": 1,
                "notes": {
                    "user_id": str(current_user.id),
                    "user_email": current_user.email or "",
                    "type": "topup",
                    "credits": str(payload.credits),
                }
            })
            order_id = rz_order["id"]
        except Exception as e:
            print(f"[Razorpay] Error creating order, fallback to mock: {e}")

    return {
        "order_id": order_id,
        "credits": payload.credits,
        "amount_inr": price_inr,
        "currency": "INR",
        "key_id": key_id or "rzp_test_placeholder",
    }


@router.post("/topup/verify")
async def verify_topup(
    payload: TopupVerifyRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Verify payment and add purchased top-up credits to topup_credit_balance.
    """
    if payload.credits < 50:
        raise HTTPException(status_code=400, detail="Minimum top-up is 50 credits.")

    price_inr = calculate_topup_price(payload.credits)
    key_secret = os.getenv("RAZORPAY_KEY_SECRET")

    # If real Razorpay credentials provided, verify HMAC signature
    if key_secret and payload.razorpay_order_id and payload.razorpay_payment_id and payload.razorpay_signature:
        generated_sig = hmac.new(
            key_secret.encode(),
            f"{payload.razorpay_order_id}|{payload.razorpay_payment_id}".encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(generated_sig, payload.razorpay_signature):
            raise HTTPException(status_code=400, detail="Payment verification failed: Invalid signature")

    # Add top-up credits
    tx = await UniversalCreditService.add_credits(
        db=db,
        user_id=current_user.id,
        amount=float(payload.credits),
        credit_type="topup_credit",
        reference_id=payload.razorpay_payment_id or payload.razorpay_order_id or f"topup_{uuid.uuid4().hex[:8]}",
        description=f"+{payload.credits} Credit Top-up (₹{price_inr})",
        metadata_json={
            "price_inr": price_inr,
            "order_id": payload.razorpay_order_id,
            "payment_id": payload.razorpay_payment_id,
        }
    )

    wallet = await UniversalCreditService.get_user_wallet(db, current_user.id)
    return {
        "success": True,
        "message": f"Successfully added {payload.credits} top-up credits to your wallet!",
        "transaction_id": tx.transaction_id,
        "wallet": wallet,
    }


# ── Admin Endpoints ──────────────────────────────────────────────────────────

@router.post("/admin/update-rate")
async def admin_update_rate(
    payload: AdminRateUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Admin: Update dynamic conversion rate.
    """
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")

    config = await UniversalCreditService.update_admin_rate(
        db=db,
        key=payload.key,
        value=payload.value,
        description=payload.description or "",
    )
    return {
        "success": True,
        "key": config.key,
        "value": config.value,
        "description": config.description,
        "updated_at": config.updated_at.isoformat() if config.updated_at else None,
    }


@router.post("/admin/adjust-credits")
async def admin_adjust_credits(
    payload: AdminCreditAdjustRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Admin: Manual credit adjustment, refund, or bonus credit.
    """
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")

    target_user = await db.get(User, payload.user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="Target user not found")

    if payload.amount > 0:
        tx = await UniversalCreditService.add_credits(
            db=db,
            user_id=target_user.id,
            amount=payload.amount,
            credit_type=payload.type,
            reference_id=f"admin_adj_{current_user.id}_{int(datetime.now().timestamp())}",
            description=payload.description or f"Admin adjustment by #{current_user.id}",
        )
    else:
        # Deduction
        tx = await UniversalCreditService.deduct_credits(
            db=db,
            user_id=target_user.id,
            service="system",
            count_or_duration=abs(payload.amount),
            reference_id=f"admin_adj_{current_user.id}_{int(datetime.now().timestamp())}",
            description=payload.description or f"Admin manual deduction by #{current_user.id}",
        )

    return {
        "success": True,
        "transaction_id": tx.transaction_id,
        "user_id": target_user.id,
        "new_balance": target_user.credits,
    }
