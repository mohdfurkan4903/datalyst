from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field

from datalyst.core.models import LLMDataPolicy, Role, User


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: User


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


class SourceCreate(BaseModel):
    name: str
    url: str
    include_schemas: list[str] = Field(default_factory=list)
    llm_data_policy: LLMDataPolicy = LLMDataPolicy.ROWS
    sync_interval_seconds: int = Field(default=300, ge=60)


class SourceUpdate(BaseModel):
    url: str | None = None
    include_schemas: list[str] | None = None
    llm_data_policy: LLMDataPolicy | None = None
    sync_interval_seconds: int | None = Field(default=None, ge=60)


class DescriptionUpdate(BaseModel):
    description: str | None = Field(default=None, max_length=2000)


class RelationshipUpdate(BaseModel):
    status: str = Field(pattern="^(active|rejected)$")


class RelationshipCreate(BaseModel):
    child: str
    parent: str


class TermUpdate(BaseModel):
    definition: str
    sql_hint: str | None = None


class ExampleCreate(BaseModel):
    question: str
    sql: str


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class QueryRequest(BaseModel):
    sql: str = Field(min_length=1, max_length=20000)


class Feedback(BaseModel):
    value: int | None = Field(default=None, ge=-1, le=1)
    note: str | None = Field(default=None, max_length=2000)


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    name: str = ""
    role: Role = Role.ANALYST


class UserUpdate(BaseModel):
    name: str | None = None
    role: Role | None = None
    active: bool | None = None
    password: str | None = None


class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class GrantUpdate(BaseModel):
    table_patterns: list[str] | None = None


class SetupRequest(BaseModel):
    email: EmailStr
    password: str
    name: str = ""


class SettingsUpdate(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    reset: list[str] = Field(default_factory=list)


class ConnectionTest(BaseModel):
    target: Literal["llm", "describe", "embeddings"] = "llm"
