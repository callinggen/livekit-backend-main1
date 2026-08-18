from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from pydantic import BaseModel
from typing import List, Optional

from app.database import get_db
from app.models.user_phone_number import UserPhoneNumber
from app.api.auth import get_current_user
from app.models.user import User

router = APIRouter(prefix="/api/user", tags=["Phone Numbers"])

class PhoneNumberResponse(BaseModel):
    id: int
    region: str
    phone_number: str
    number_type: str = "Mobile"
    provider_name: str
    provider_account_id: Optional[str] = None
    api_key_auth_token: Optional[str] = None
    sip_id: Optional[str] = None
    sip_username: Optional[str] = None
    sip_password: Optional[str] = None
    status: str = "Active"
    is_default: bool = False

    class Config:
        from_attributes = True

class AssignPhoneNumberRequest(BaseModel):
    user_id: int
    region: str
    phone_number: str
    number_type: str = "Mobile"
    provider_name: str
    provider_account_id: Optional[str] = None
    api_key_auth_token: Optional[str] = None
    sip_id: Optional[str] = None
    sip_username: Optional[str] = None
    sip_password: Optional[str] = None
    status: str = "Active"
    is_default: bool = False


DEFAULT_SYSTEM_NUMBERS = [
    {
        "id": 1,
        "region": "India (+91)",
        "phone_number": "+91 98857 33334",
        "number_type": "Mobile",
        "provider_name": "Tata Communications",
        "provider_account_id": "ACC-TATA-IN-01",
        "api_key_auth_token": "sk_tata_live_99812",
        "sip_id": "sip-trunk-tata-in",
        "sip_username": "tata_sip_user",
        "sip_password": "••••••••",
        "status": "Active",
        "is_default": True
    },
    {
        "id": 2,
        "region": "United States (+1)",
        "phone_number": "+1 (800) 555-0199",
        "number_type": "Toll-Free",
        "provider_name": "Twilio US",
        "provider_account_id": "AC10992384710293",
        "api_key_auth_token": "sk_twilio_us_00192",
        "sip_id": "sip-trunk-twilio-us",
        "sip_username": "twilio_sip_user",
        "sip_password": "••••••••",
        "status": "Active",
        "is_default": False
    },
    {
        "id": 3,
        "region": "United Kingdom (+44)",
        "phone_number": "+44 20 7946 0991",
        "number_type": "Landline",
        "provider_name": "Plivo Global",
        "provider_account_id": "MA98230192830192",
        "api_key_auth_token": "sk_plivo_uk_88721",
        "sip_id": "sip-trunk-plivo-uk",
        "sip_username": "plivo_sip_user",
        "sip_password": "••••••••",
        "status": "Active",
        "is_default": False
    }
]


@router.get("/phone-numbers", response_model=List[PhoneNumberResponse])
async def get_user_phone_numbers(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Returns active assigned phone numbers for the logged-in user.
    For regular users: hides provider platform credentials (only Region & Phone Number shown).
    For admins: returns complete telephony metadata.
    """
    stmt = (
        select(UserPhoneNumber)
        .where(UserPhoneNumber.user_id == current_user.id)
        .where(UserPhoneNumber.is_active == True)
    )
    result = await db.execute(stmt)
    user_numbers = result.scalars().all()

    if not user_numbers:
        raw_list = DEFAULT_SYSTEM_NUMBERS
        if not current_user.is_admin:
            # Mask provider platform details for non-admin user
            masked = []
            for num in raw_list:
                item = dict(num)
                item["provider_name"] = "Assigned Line"
                item["provider_account_id"] = None
                item["api_key_auth_token"] = None
                item["sip_id"] = None
                item["sip_username"] = None
                item["sip_password"] = None
                masked.append(item)
            return [PhoneNumberResponse(**num) for num in masked]
        return [PhoneNumberResponse(**num) for num in raw_list]

    if not current_user.is_admin:
        res = []
        for num in user_numbers:
            res.append(
                PhoneNumberResponse(
                    id=num.id,
                    region=num.region,
                    phone_number=num.phone_number,
                    number_type=num.number_type,
                    provider_name="Assigned Line",
                    provider_account_id=None,
                    api_key_auth_token=None,
                    sip_id=None,
                    sip_username=None,
                    sip_password=None,
                    status=num.status,
                    is_default=num.is_default,
                )
            )
        return res

    return user_numbers


@router.post("/assign-phone-number", response_model=PhoneNumberResponse)
async def assign_phone_number(
    request: AssignPhoneNumberRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Admin endpoint to assign a telephone number with full SIP & provider credentials to a user.
    """
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only administrators can assign phone numbers."
        )

    # If setting as default, clear other default numbers for user
    if request.is_default:
        existing_stmt = select(UserPhoneNumber).where(UserPhoneNumber.user_id == request.user_id)
        res = await db.execute(existing_stmt)
        for existing_num in res.scalars().all():
            existing_num.is_default = False

    new_number = UserPhoneNumber(
        user_id=request.user_id,
        region=request.region,
        phone_number=request.phone_number,
        number_type=request.number_type,
        provider_name=request.provider_name,
        provider_account_id=request.provider_account_id,
        api_key_auth_token=request.api_key_auth_token,
        sip_id=request.sip_id,
        sip_username=request.sip_username,
        sip_password=request.sip_password,
        status=request.status,
        is_default=request.is_default,
        is_active=True,
    )

    db.add(new_number)
    await db.commit()
    await db.refresh(new_number)

    return new_number
