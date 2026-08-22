from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.schemas.payment import (
    PaymentCreateOrderRequest,
    PaymentCreateOrderResponse,
    PaymentVerifyRequest,
    PaymentVerifyResponse,
)
from app.services.payment_service import PaymentService

router = APIRouter(prefix="/payments", tags=["Payments"])

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
            plan_name=payload.plan_name
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
