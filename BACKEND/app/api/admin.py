from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.database import get_db
from app.models.user import User
from app.schemas.auth import UserCreateRequest
from app.core.security import get_current_admin_user, get_password_hash
from app.services.email_service import email_service
import secrets

router = APIRouter()

@router.post("/users", status_code=status.HTTP_201_CREATED)
async def create_user(
    user_data: UserCreateRequest,
    current_admin: User = Depends(get_current_admin_user),
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

    # Generate temporary password
    temp_password = secrets.token_urlsafe(12)

    # Create user
    new_user = User(
        full_name=user_data.full_name,
        email=user_data.email,
        phone_number=user_data.phone_number,
        hashed_password=get_password_hash(temp_password),
        is_first_login=True,
        is_admin=False
    )
    
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    
    # Send welcome email with temporary password
    if user_data.email:
        try:
            email_service.send_welcome_email(user_data.email, temp_password)
        except Exception as e:
            print(f"Failed to send welcome email to {user_data.email}: {e}")
    
    return {
        "message": "User created successfully",
        "user": {
            "id": new_user.id,
            "email": new_user.email,
            "full_name": new_user.full_name
        }
    }
