from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app.models.user import User
from app.core.security import get_password_hash
from app.services.email_service import email_service
import secrets

async def provision_user(
    db: AsyncSession,
    full_name: str,
    email: str,
    phone_number: str = None,
    is_admin: bool = False
) -> dict:
    # Verify email uniqueness
    stmt = select(User).where(User.email == email)
    result = await db.execute(stmt)
    existing_user = result.scalars().first()
    if existing_user:
        return {"success": False, "message": "Email already in use."}
        
    # Verify phone uniqueness if provided
    if phone_number:
        stmt = select(User).where(User.phone_number == phone_number)
        result = await db.execute(stmt)
        existing_phone = result.scalars().first()
        if existing_phone:
            return {"success": False, "message": "Phone number already in use."}

    # Generate and hash temporary password
    temp_password = secrets.token_urlsafe(12)
    hashed_password = get_password_hash(temp_password)
    
    # Create user in DB and commit transaction
    new_user = User(
        email=email,
        full_name=full_name,
        phone_number=phone_number or None,
        hashed_password=hashed_password,
        is_first_login=True,
        is_admin=is_admin,
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)

    # Attempt to send welcome email
    try:
        email_service.send_welcome_email(to_email=email, temp_password=temp_password)
    except Exception as e:
        # Cleanup on failure: email failed after commit, rollback user creation
        await db.delete(new_user)
        await db.commit()
        print(f"Rolling back user creation for {email} due to SMTP failure: {e}")
        return {"success": False, "message": "Unable to send welcome email. User provisioning rolled back."}

    return {"success": True, "message": "User provisioned successfully."}
