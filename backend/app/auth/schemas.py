"""Auth API DTOs.

``confirm_password`` is validated at this request boundary only; it is never
persisted.
"""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LENGTH = 8


class RegisterCommand(BaseModel):
    email: str
    password: str
    confirm_password: str

    @field_validator("email")
    @classmethod
    def _validate_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not EMAIL_RE.match(normalized):
            raise ValueError("邮箱格式不正确")
        return normalized

    @field_validator("password")
    @classmethod
    def _validate_password(cls, value: str) -> str:
        if len(value) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"密码至少 {MIN_PASSWORD_LENGTH} 位")
        return value

    @model_validator(mode="after")
    def _passwords_match(self) -> RegisterCommand:
        if self.password != self.confirm_password:
            raise ValueError("两次输入的密码不一致")
        return self


class LoginCommand(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def _validate_email(cls, value: str) -> str:
        return value.strip().lower()


class ChangePasswordCommand(BaseModel):
    current_password: str
    new_password: str
    confirm_password: str

    @field_validator("new_password")
    @classmethod
    def _validate_new_password(cls, value: str) -> str:
        if len(value) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"密码至少 {MIN_PASSWORD_LENGTH} 位")
        return value

    @model_validator(mode="after")
    def _passwords_match(self) -> ChangePasswordCommand:
        if self.new_password != self.confirm_password:
            raise ValueError("两次输入的密码不一致")
        return self


class UserDto(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    display_name: str | None
    created_at: datetime


class SessionDto(BaseModel):
    id: int
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    is_current: bool


class AuthResponse(BaseModel):
    user: UserDto
    session: SessionDto


class SessionsResponse(BaseModel):
    sessions: list[SessionDto]
