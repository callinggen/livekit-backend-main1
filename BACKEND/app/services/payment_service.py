import os
import uuid
import json
from datetime import datetime, timezone
from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
import razorpay
from razorpay.errors import SignatureVerificationError

from app.models.payment import Payment
from app.models.user import User

from dotenv import load_dotenv
load_dotenv()

# Load environment variables
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "mock_key_id")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "mock_key_secret")
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "mock_webhook_secret")


# Determine Mock Mode status
# We default to mock mode if real keys aren't set or are default placeholders
IS_MOCK_MODE = (
    not RAZORPAY_KEY_ID
    or not RAZORPAY_KEY_SECRET
    or RAZORPAY_KEY_ID.startswith("mock_")
    or RAZORPAY_KEY_SECRET.startswith("mock_")
)

# Standard pricing and plan configuration (authoritative backend source)
PLANS = {
    "Starter": {
        "amount": 299900,  # in paise (₹2,999)
        "credits": 2000,
    },
    "Growth": {
        "amount": 699900,  # in paise (₹6,999)
        "credits": 5000,
    },
    "Pro": {
        "amount": 1299900, # in paise (₹12,999)
        "credits": 10000,
    },
    "Business": {
        "amount": 2999900, # in paise (₹29,999)
        "credits": 25000,
    }
}

class PaymentService:
    @staticmethod
    def get_razorpay_client():
        if IS_MOCK_MODE:
            return None
        try:
            return razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
        except Exception as e:
            print(f"Error initializing Razorpay Client: {e}")
            return None

    @classmethod
    async def create_order(cls, db: AsyncSession, user_id: int, plan_name: str) -> dict:
        # Validate plan selection
        if plan_name not in PLANS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid plan selected"
            )

        plan = PLANS[plan_name]
        amount = plan["amount"]
        credits = plan["credits"]

        if IS_MOCK_MODE:
            # Generate a mock razorpay order ID
            razorpay_order_id = f"order_mock_{uuid.uuid4().hex[:12]}"
            print(f"[MOCK MODE] Generated mock Razorpay order: {razorpay_order_id} for user {user_id}")
        else:
            client = cls.get_razorpay_client()
            if not client:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Payment Gateway is currently unavailable"
                )
            
            try:
                # Create order inside Razorpay API
                order_data = {
                    "amount": amount,
                    "currency": "INR",
                    "receipt": f"receipt_{user_id}_{int(datetime.now().timestamp())}",
                    "payment_capture": 1
                }
                order = client.order.create(data=order_data)
                razorpay_order_id = order["id"]
            except Exception as e:
                print(f"Razorpay Order Creation Failed: {e}")
                detail = "Payment Gateway error during order creation"
                if "authentication failed" in str(e).lower():
                    detail = "Razorpay API Authentication Failed. Please check if your API Key ID or Key Secret is correct/truncated."
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=detail
                )


        # Log pending transaction in local database
        db_payment = Payment(
            user_id=user_id,
            plan_name=plan_name,
            amount=amount,
            currency="INR",
            credits=credits,
            razorpay_order_id=razorpay_order_id,
            status="pending"
        )
        db.add(db_payment)
        await db.commit()
        await db.refresh(db_payment)

        return {
            "razorpay_order_id": razorpay_order_id,
            "amount": amount,
            "currency": "INR",
            "key_id": RAZORPAY_KEY_ID,
            "plan_name": plan_name
        }

    @classmethod
    async def verify_payment_frontend(
        cls,
        db: AsyncSession,
        user_id: int,
        razorpay_order_id: str,
        razorpay_payment_id: str,
        razorpay_signature: str
    ) -> Payment:
        # 1. Look up local payment record
        stmt = select(Payment).where(Payment.razorpay_order_id == razorpay_order_id)
        result = await db.execute(stmt)
        payment = result.scalars().first()

        if not payment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Order not found"
            )

        # 2. Check user ownership
        if payment.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Unauthorized access to this order"
            )

        # 3. Check if order is already processed (Idempotency checkpoint)
        if payment.status == "success":
            print(f"Order {razorpay_order_id} already marked as success. Returning early.")
            return payment

        # 4. Perform Signature Verification
        if IS_MOCK_MODE:
            # Under Mock mode, verify that signature matches a mock signature format
            print(f"[MOCK MODE] Verifying mock payment: {razorpay_payment_id}")
            if not razorpay_signature or not razorpay_payment_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid payment verification parameters"
                )
        else:
            client = cls.get_razorpay_client()
            if not client:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Payment Gateway is currently unavailable"
                )
            
            try:
                # Calls Razorpay signature verification utility
                client.utility.verify_payment_signature({
                    'razorpay_order_id': razorpay_order_id,
                    'razorpay_payment_id': razorpay_payment_id,
                    'razorpay_signature': razorpay_signature
                })
            except SignatureVerificationError as e:
                print(f"Razorpay Payment Verification Failed: {e}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Payment verification failed: signature mismatch"
                )

        # 5. Process payment status updates and credits allocation
        return await cls._process_successful_payment(
            db=db,
            payment=payment,
            payment_id=razorpay_payment_id,
            signature=razorpay_signature
        )

    @classmethod
    async def verify_webhook(
        cls,
        db: AsyncSession,
        payload_bytes: bytes,
        signature: str
    ) -> dict:
        # 1. Webhook Signature Verification
        if not IS_MOCK_MODE:
            client = cls.get_razorpay_client()
            if not client:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Payment Gateway unavailable"
                )
            try:
                client.utility.verify_webhook_signature(
                    payload_bytes.decode("utf-8"),
                    signature,
                    RAZORPAY_WEBHOOK_SECRET
                )
            except SignatureVerificationError as e:
                print(f"Webhook signature mismatch: {e}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid webhook signature"
                )

        # 2. Extract transaction details
        try:
            event_data = json.loads(payload_bytes.decode("utf-8"))
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid JSON payload"
            )

        event_name = event_data.get("event")
        print(f"Processing webhook event: {event_name}")

        if event_name != "payment.captured":
            return {"status": "ignored", "message": f"Event '{event_name}' is not handled."}

        payment_entity = event_data.get("payload", {}).get("payment", {}).get("entity", {})
        razorpay_order_id = payment_entity.get("order_id")
        razorpay_payment_id = payment_entity.get("id")
        webhook_amount = payment_entity.get("amount")

        if not razorpay_order_id or not razorpay_payment_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing order_id or payment_id in webhook payload"
            )

        # 3. Look up Payment record
        stmt = select(Payment).where(Payment.razorpay_order_id == razorpay_order_id)
        result = await db.execute(stmt)
        payment = result.scalars().first()

        if not payment:
            # Order may belong to another gateway or test, or database discrepancy
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Order {razorpay_order_id} not found in system"
            )

        # 4. Security Check: Validate amount captured in payload matches intended plan pricing
        if webhook_amount != payment.amount:
            print(f"CRITICAL: Webhook captured amount {webhook_amount} does not match order amount {payment.amount}!")
            # Mark transaction as failed due to pricing spoof/discrepancy
            payment.status = "failed"
            payment.updated_at = datetime.now(timezone.utc)
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Amount mismatch inside webhook payload"
            )

        # 5. Process transaction with shared logic
        await cls._process_successful_payment(
            db=db,
            payment=payment,
            payment_id=razorpay_payment_id,
            signature=signature
        )

        return {"status": "success", "message": "Webhook processed successfully"}

    @classmethod
    async def _process_successful_payment(
        cls,
        db: AsyncSession,
        payment: Payment,
        payment_id: str,
        signature: str
    ) -> Payment:
        # OPTIMISTIC CONCURRENCY CONTROL (OCC)
        # We try to update the status to 'success' ONLY if it is currently 'pending'.
        # SQLite serializes writes, guaranteeing only one transaction updates the row first.
        stmt = (
            update(Payment)
            .where(Payment.id == payment.id)
            .where(Payment.status == "pending")
            .values(
                status="success",
                razorpay_payment_id=payment_id,
                razorpay_signature=signature,
                updated_at=datetime.now(timezone.utc)
            )
        )
        res = await db.execute(stmt)
        
        if res.rowcount == 0:
            # This transaction was a runner-up (another verification thread won).
            # Retrieve the already-updated database record to return E2E values safely.
            stmt_select = select(Payment).where(Payment.id == payment.id)
            res_select = await db.execute(stmt_select)
            updated_payment = res_select.scalars().first()
            print(f"Idempotency: Order {payment.razorpay_order_id} was already processed.")
            return updated_payment

        # We are the winner! Safely add credits to user account.
        user_stmt = select(User).where(User.id == payment.user_id)
        user_res = await db.execute(user_stmt)
        user = user_res.scalars().first()

        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found for credit allocation"
            )

        # Add credits
        old_credits = user.credits
        user.credits += payment.credits
        print(f"Successfully processed payment. Allocated {payment.credits} credits to User {user.id}. Balance: {old_credits} -> {user.credits}")

        # Commit all modifications to users & payments tables
        await db.commit()
        await db.refresh(payment)

        # Trigger confirmation email in the background to prevent blocking uvicorn
        try:
            import asyncio
            from app.services.email_service import email_service
            asyncio.create_task(
                asyncio.to_thread(
                    email_service.send_payment_invoice_email,
                    to_email=user.email,
                    full_name=user.full_name,
                    plan_name=payment.plan_name,
                    amount=payment.amount,
                    credits=payment.credits,
                    order_id=payment.razorpay_order_id,
                    payment_id=payment_id
                )
            )
            print(f"Dispatched purchase confirmation email task in background to {user.email}")
        except Exception as email_err:
            print(f"Failed to dispatch payment confirmation email: {email_err}")

        return payment

