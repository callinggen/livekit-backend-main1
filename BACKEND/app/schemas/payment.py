from pydantic import BaseModel
from typing import Optional

class PaymentCreateOrderRequest(BaseModel):
    plan_name: str

class PaymentCreateOrderResponse(BaseModel):
    razorpay_order_id: str
    amount: int
    currency: str
    key_id: str
    plan_name: str

class PaymentVerifyRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str

class PaymentVerifyResponse(BaseModel):
    status: str
    message: str
    credits: int
