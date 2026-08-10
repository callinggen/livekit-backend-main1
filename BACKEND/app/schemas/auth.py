from pydantic import BaseModel, EmailStr
from typing import List, Optional
from app.schemas.agent import AgentCreate

class Token(BaseModel):
    access_token: str
    token_type: str
    full_name: str | None = None
    is_first_login: bool = False
    is_admin: bool = False
    refresh_token: str | None = None
    credits: int = 2000
    subscription_plan: str | None = None
    company_name: str | None = None
    industry: str | None = None
    phone_number: str | None = None
    agent_name: str | None = None
    agent_language: str | None = None
    agent_voice: str | None = None
    agent_script: str | None = None

class TokenPayload(BaseModel):
    sub: str | None = None
    is_first_login: bool = False
    is_admin: bool = False
    iat: float | int | None = None

class LoginRequest(BaseModel):
    identifier: str
    password: str | None = None

class UserResponse(BaseModel):
    id: int
    email: str | None = None
    phone_number: str | None = None
    credits: int = 2000
    subscription_plan: str | None = None

    class Config:
        from_attributes = True

class ForgotPasswordRequest(BaseModel):
    email: str

class VerifyResetCodeRequest(BaseModel):
    email: str
    reset_code: str

class ResetPasswordRequest(BaseModel):
    email: str
    reset_code: str
    new_password: str

class ChangePasswordRequest(BaseModel):
    new_password: str

class UserCreateRequest(BaseModel):
    full_name: str
    email: EmailStr
    phone_number: str | None = None
    password: str | None = None
    subscription_plan: str | None = None
    credits: int | None = None
    company_name: str | None = None
    industry: str | None = None
    agent_name: str | None = None
    agent_language: str | None = None
    agent_voice: str | None = None
    agent_script: str | None = None

class RegisterRequest(BaseModel):
    full_name: str
    email: EmailStr
    phone_number: str | None = None
    password: str
    subscription_plan: str | None = None
    credits: int | None = None
    company_name: str | None = None
    industry: str | None = None
    agent_name: str | None = None
    agent_language: str | None = None
    agent_voice: str | None = None
    agent_script: str | None = None
    agents: Optional[List[AgentCreate]] = None

class ProfileUpdateRequest(BaseModel):
    full_name: str | None = None
    phone_number: str | None = None
    company_name: str | None = None
    industry: str | None = None
    agent_name: str | None = None
    agent_language: str | None = None
    agent_voice: str | None = None
    agent_script: str | None = None
