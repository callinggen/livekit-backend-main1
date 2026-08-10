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
from app.models.agent import Agent
from app.schemas.auth import LoginRequest, Token, ForgotPasswordRequest, VerifyResetCodeRequest, ResetPasswordRequest, ChangePasswordRequest, UserCreateRequest, RegisterRequest, ProfileUpdateRequest
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
        refresh_token=None,
        credits=user.credits,
        subscription_plan=user.subscription_plan,
        company_name=getattr(user, "company_name", None),
        industry=getattr(user, "industry", None),
        phone_number=user.phone_number,
        agent_name=getattr(user, "agent_name", None),
        agent_language=getattr(user, "agent_language", None),
        agent_voice=getattr(user, "agent_voice", None),
        agent_script=getattr(user, "agent_script", None),
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
        "refresh_token": None,
        "credits": current_user.credits,
        "subscription_plan": current_user.subscription_plan
    }

from sqlalchemy import func

@router.post("/forgot-password")
async def forgot_password(
    data: ForgotPasswordRequest, db: AsyncSession = Depends(get_db)
):
    clean_email = data.email.strip().lower() if data.email else ""
    if not clean_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email address is required."
        )

    stmt = select(User).where(func.lower(User.email) == clean_email)
    result = await db.execute(stmt)
    user = result.scalars().first()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No account exists with email '{clean_email}'. Please check your email or register."
        )
        
    reset_code = ''.join(random.choices(string.digits, k=6))
    expires_at = datetime.utcnow() + timedelta(minutes=15)
    
    reset_entry = PasswordReset(
        email=clean_email,
        reset_code=reset_code,
        expires_at=expires_at
    )
    db.add(reset_entry)
    await db.commit()
    
    # Print to console for easy development testing
    print(f"\n==================================================")
    print(f"[PASSWORD RESET OTP] Code for {clean_email}: {reset_code}")
    print(f"==================================================\n")

    try:
        email_service.send_password_reset_email(clean_email, reset_code)
    except Exception as e:
        print(f"Email sending failed: {e}")
        # Delete the reset entry since we failed to send the email
        await db.delete(reset_entry)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to send email to {clean_email}. Error: {str(e)}"
        )
        
    return {"message": f"Verification code sent to {clean_email}. Please check your inbox and Spam folder."}

@router.post("/verify-reset-code")
async def verify_reset_code(
    data: VerifyResetCodeRequest, db: AsyncSession = Depends(get_db)
):
    clean_email = data.email.strip().lower() if data.email else ""
    stmt = select(PasswordReset).where(
        func.lower(PasswordReset.email) == clean_email,
        PasswordReset.reset_code == data.reset_code.strip(),
        PasswordReset.expires_at > datetime.utcnow()
    )
    result = await db.execute(stmt)
    reset_entry = result.scalars().first()
    
    if not reset_entry:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification code."
        )
        
    return {"message": "Code verified."}

@router.post("/reset-password")
async def reset_password(
    data: ResetPasswordRequest, db: AsyncSession = Depends(get_db)
):
    clean_email = data.email.strip().lower() if data.email else ""
    stmt = select(PasswordReset).where(
        func.lower(PasswordReset.email) == clean_email,
        PasswordReset.reset_code == data.reset_code.strip(),
        PasswordReset.expires_at > datetime.utcnow()
    )
    result = await db.execute(stmt)
    reset_entry = result.scalars().first()
    
    if not reset_entry:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification code."
        )
        
    try:
        validate_password_policy(data.new_password)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

    stmt = select(User).where(func.lower(User.email) == clean_email)
    result = await db.execute(stmt)
    user = result.scalars().first()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found."
        )
        
    user.hashed_password = get_password_hash(data.new_password)
    user.is_first_login = False
    user.password_changed_at = datetime.utcnow()
    
    await db.delete(reset_entry)
    await db.commit()
    
    return {"message": "Password reset successfully."}

@router.get("/user/credits")
async def get_user_credits(current_user: User = Depends(get_current_user)):
    return {
        "credits": current_user.credits
    }

@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register_user(
    user_data: RegisterRequest,
    db: AsyncSession = Depends(get_db)
):
    # Validate email uniqueness
    if user_data.email:
        stmt = select(User).where(User.email == user_data.email)
        result = await db.execute(stmt)
        if result.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email is already registered"
            )

    # Validate phone uniqueness
    if user_data.phone_number:
        stmt = select(User).where(User.phone_number == user_data.phone_number)
        result = await db.execute(stmt)
        if result.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Phone number is already registered"
            )

    if not user_data.email and not user_data.phone_number:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Must provide either email or phone number"
        )

    try:
        validate_password_policy(user_data.password)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

    # Create user
    new_user = User(
        full_name=user_data.full_name,
        email=user_data.email,
        phone_number=user_data.phone_number,
        hashed_password=get_password_hash(user_data.password),
        is_first_login=False,
        is_admin=False,
        subscription_plan=user_data.subscription_plan,
        company_name=user_data.company_name,
        industry=user_data.industry,
        agent_name=user_data.agent_name,
        agent_language=user_data.agent_language,
        agent_voice=user_data.agent_voice,
        agent_script=user_data.agent_script,
    )
    
    if user_data.credits is not None:
        new_user.credits = user_data.credits

    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    
    # Create associated agents if provided
    if user_data.agents:
        for agent_data in user_data.agents:
            new_agent = Agent(
                user_id=new_user.id,
                name=agent_data.name,
                language=agent_data.language,
                voice=agent_data.voice,
                script=agent_data.script
            )
            db.add(new_agent)
        await db.commit()
    
    return {
        "message": "User registered successfully",
        "user": {
            "id": new_user.id,
            "email": new_user.email,
            "full_name": new_user.full_name,
            "company_name": new_user.company_name,
            "industry": new_user.industry,
            "agent_name": new_user.agent_name,
            "agent_language": new_user.agent_language,
            "agent_voice": new_user.agent_voice,
        }
    }


@router.get("/me")
async def get_me(current_user: User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "email": current_user.email,
        "phone_number": current_user.phone_number,
        "full_name": current_user.full_name,
        "company_name": getattr(current_user, "company_name", None),
        "industry": getattr(current_user, "industry", None),
        "credits": current_user.credits,
        "is_first_login": current_user.is_first_login,
        "is_admin": current_user.is_admin,
        "subscription_plan": current_user.subscription_plan,
        "agent_name": getattr(current_user, "agent_name", None),
        "agent_language": getattr(current_user, "agent_language", None),
        "agent_voice": getattr(current_user, "agent_voice", None),
        "agent_script": getattr(current_user, "agent_script", None),
    }

@router.put("/profile")
async def update_my_profile(
    data: ProfileUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    if data.full_name is not None:
        current_user.full_name = data.full_name
    if data.company_name is not None:
        current_user.company_name = data.company_name
    if data.industry is not None:
        current_user.industry = data.industry
    if data.phone_number is not None:
        current_user.phone_number = data.phone_number
    if data.agent_name is not None:
        current_user.agent_name = data.agent_name
    if data.agent_language is not None:
        current_user.agent_language = data.agent_language
    if data.agent_voice is not None:
        current_user.agent_voice = data.agent_voice
    if data.agent_script is not None:
        current_user.agent_script = data.agent_script

    await db.commit()
    await db.refresh(current_user)

    return {
        "message": "Profile updated successfully",
        "user": {
            "id": current_user.id,
            "email": current_user.email,
            "full_name": current_user.full_name,
            "company_name": getattr(current_user, "company_name", None),
            "industry": getattr(current_user, "industry", None),
            "phone_number": current_user.phone_number,
            "credits": current_user.credits,
            "subscription_plan": current_user.subscription_plan,
            "agent_name": getattr(current_user, "agent_name", None),
            "agent_language": getattr(current_user, "agent_language", None),
            "agent_voice": getattr(current_user, "agent_voice", None),
            "agent_script": getattr(current_user, "agent_script", None),
        }
    }

