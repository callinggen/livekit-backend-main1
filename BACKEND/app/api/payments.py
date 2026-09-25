from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.models.payment import Payment
from app.schemas.payment import (
    PaymentCreateOrderRequest,
    PaymentCreateOrderResponse,
    PaymentVerifyRequest,
    PaymentVerifyResponse,
)
from app.services.payment_service import PaymentService

router = APIRouter(prefix="/payments", tags=["Payments"])

@router.get("/history")
async def get_payment_history(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        stmt = (
            select(Payment)
            .where(Payment.user_id == current_user.id)
            .order_by(Payment.created_at.desc())
        )
        result = await db.execute(stmt)
        payments = result.scalars().all()
        return [
            {
                "id": p.id,
                "plan_name": p.plan_name,
                "amount": p.amount,
                "currency": p.currency,
                "credits": p.credits,
                "razorpay_order_id": p.razorpay_order_id,
                "razorpay_payment_id": p.razorpay_payment_id,
                "status": p.status,
                "created_at": p.created_at.isoformat() if p.created_at else None,
                "updated_at": p.updated_at.isoformat() if p.updated_at else None,
            }
            for p in payments
        ]
    except Exception as e:
        print(f"API Error in get_payment_history: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve payment history"
        )

@router.post("/create-order", response_model=PaymentCreateOrderResponse)
async def create_order(
    payload: PaymentCreateOrderRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        order_details = await PaymentService.create_order(
            db=db,
            user_id=current_user.id,
            plan_name=payload.plan_name,
            custom_credits=payload.custom_credits,
        )
        return order_details
    except HTTPException as e:
        raise e
    except Exception as e:
        print(f"API Error in create-order: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred during order creation"
        )

@router.post("/verify", response_model=PaymentVerifyResponse)
async def verify_payment(
    payload: PaymentVerifyRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        payment = await PaymentService.verify_payment_frontend(
            db=db,
            user_id=current_user.id,
            razorpay_order_id=payload.razorpay_order_id,
            razorpay_payment_id=payload.razorpay_payment_id,
            razorpay_signature=payload.razorpay_signature
        )
        # Return updated credit balance
        return {
            "status": payment.status,
            "message": "Payment verified successfully",
            "credits": current_user.credits
        }
    except HTTPException as e:
        raise e
    except Exception as e:
        print(f"API Error in verify: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred during payment verification"
        )

@router.post("/webhook")
async def webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    body_bytes = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")
    
    try:
        res = await PaymentService.verify_webhook(
            db=db,
            payload_bytes=body_bytes,
            signature=signature
        )
        return res
    except HTTPException as e:
        raise e
    except Exception as e:
        print(f"API Error in webhook: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred during webhook execution"
        )
