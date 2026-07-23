from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from datetime import datetime, timedelta, timezone
from pydantic import EmailStr, TypeAdapter, ValidationError
import random
import string
import re
import secrets

from app.database import get_db
from app.models.user import User
from app.models.password_reset import PasswordReset
from app.schemas.auth import LoginRequest, Token, ForgotPasswordRequest, VerifyResetCodeRequest, ResetPasswordRequest, ChangePasswordRequest, UserCreateRequest
from app.core.security import verify_password, create_access_token, get_password_hash, get_current_user
from app.services.email_service import email_service

router = APIRouter()
email_validator = TypeAdapter(EmailStr)

def validate_password_policy(password: str):
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters long")
    if not re.search(r"[A-Z]", password):
        raise ValueError("Password must contain at least one uppercase letter")
    if not re.search(r"[a-z]", password):
        raise ValueError("Password must contain at least one lowercase letter")
    if not re.search(r"\d", password):
        raise ValueError("Password must contain at least one number")
    if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", password):
        raise ValueError("Password must contain at least one special character")

@router.post("/login")
async def login(
    login_data: LoginRequest, db: AsyncSession = Depends(get_db)
):
    identifier = login_data.identifier
    
    # Try fetching by email first
    stmt = select(User).where(User.email == identifier)
    result = await db.execute(stmt)
    user = result.scalars().first()
    
    if not user:
        # Try fetching by phone
        stmt = select(User).where(User.phone_number == identifier)
        result = await db.execute(stmt)
        user = result.scalars().first()
        
    if not user:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={
                "status": "user_not_found",
                "message": "No account exists with this email."
            }
        )
        
    if not login_data.password or not verify_password(login_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incorrect email/phone number or password",
        )
        
    # Update last login
    user.last_login_at = datetime.utcnow()
    await db.commit()
        
    return Token(
        access_token=create_access_token(
            subject=user.id, 
            is_first_login=user.is_first_login, 
            is_admin=user.is_admin
        ),
        token_type="bearer",
        full_name=user.full_name,
        is_first_login=user.is_first_login,
        is_admin=user.is_admin,
        refresh_token=None
    )




@router.post("/change-password", response_model=Token)
async def change_password(
    data: ChangePasswordRequest, 
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    # Verify new password is not the same as the old one
    if verify_password(data.new_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password cannot be the same as the current password."
        )

    try:
        validate_password_policy(data.new_password)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

    current_user.hashed_password = get_password_hash(data.new_password)
    current_user.is_first_login = False
    current_user.password_changed_at = datetime.utcnow()
    
    await db.commit()
    
    return {
        "access_token": create_access_token(
            subject=current_user.id, 
            is_first_login=False, 
            is_admin=current_user.is_admin
        ),
        "token_type": "bearer",
        "full_name": current_user.full_name,
        "is_first_login": False,
        "is_admin": current_user.is_admin,
        "refresh_token": None
    }

@router.post("/forgot-password")
async def forgot_password(
    data: ForgotPasswordRequest, db: AsyncSession = Depends(get_db)
):
    stmt = select(User).where(User.email == data.email)
    result = await db.execute(stmt)
    user = result.scalars().first()
    
    if not user:
        return {"message": "If that email exists, a reset code has been sent."}
        
    reset_code = ''.join(random.choices(string.digits, k=6))
    expires_at = datetime.utcnow() + timedelta(minutes=15)
    
    reset_entry = PasswordReset(
        email=data.email,
        reset_code=reset_code,
        expires_at=expires_at
    )
    db.add(reset_entry)
    await db.commit()
    
    try:
        email_service.send_password_reset_email(data.email, reset_code)
    except Exception as e:
        print(f"Email sending failed: {e}")
        # Delete the reset entry since we failed to send the email
        await db.delete(reset_entry)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send verification code email. Please try again later."
        )
        
    return {"message": "If that email exists, a reset code has been sent."}

@router.post("/verify-reset-code")
async def verify_reset_code(
    data: VerifyResetCodeRequest, db: AsyncSession = Depends(get_db)
):
    stmt = select(PasswordReset).where(
        PasswordReset.email == data.email,
        PasswordReset.reset_code == data.reset_code,
        PasswordReset.expires_at > datetime.utcnow()
    )
    result = await db.execute(stmt)
    reset_entry = result.scalars().first()
    
    if not reset_entry:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset code."
        )
        
    return {"message": "Code verified."}

@router.post("/reset-password")
async def reset_password(
    data: ResetPasswordRequest, db: AsyncSession = Depends(get_db)
):
    stmt = select(PasswordReset).where(
        PasswordReset.email == data.email,
        PasswordReset.reset_code == data.reset_code,
        PasswordReset.expires_at > datetime.utcnow()
    )
    result = await db.execute(stmt)
    reset_entry = result.scalars().first()
    
    if not reset_entry:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset code."
        )
        
    try:
        validate_password_policy(data.new_password)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

    stmt = select(User).where(User.email == data.email)
    result = await db.execute(stmt)
    user = result.scalars().first()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User not found."
        )
        
    user.hashed_password = get_password_hash(data.new_password)
    user.is_first_login = False
    user.password_changed_at = datetime.utcnow()
    
    await db.delete(reset_entry)
    await db.commit()
    
    return {"message": "Password reset successfully."}

@router.get("/me")
async def get_me(current_user: User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "email": current_user.email,
        "full_name": current_user.full_name,
        "is_first_login": current_user.is_first_login,
        "is_admin": current_user.is_admin
    }

