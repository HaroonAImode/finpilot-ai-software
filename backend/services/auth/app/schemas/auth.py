from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator


class SignupRequest(BaseModel):
    company_name: str = Field(min_length=1, max_length=255)
    full_name: str = Field(min_length=1, max_length=255)
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)

    @field_validator("password")
    @classmethod
    def password_must_be_reasonable(cls, value: str) -> str:
        # A length floor beats complexity rules: "Passw0rd!" satisfies most
        # complexity checks and is trivially cracked, while a long passphrase is
        # both easier to remember and far stronger.
        if value.strip() != value:
            raise ValueError("Password must not start or end with a space")
        if value.lower() in {"password1234", "123456789012", "qwertyuiopas"}:
            raise ValueError("That password is too common")
        return value

    @field_validator("email")
    @classmethod
    def normalise_email(cls, value: str) -> str:
        return value.strip().lower()


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def normalise_email(cls, value: str) -> str:
        return value.strip().lower()


class UserResponse(BaseModel):
    id: UUID
    email: str
    full_name: str
    role: str
    company_id: UUID
    company_name: str
    created_at: datetime


class TokenResponse(BaseModel):
    """Only the access token is returned in the body.

    The refresh token goes back as an httpOnly cookie (architecture report §19)
    so page JavaScript — and therefore any XSS on the page — cannot read it.
    """

    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse
