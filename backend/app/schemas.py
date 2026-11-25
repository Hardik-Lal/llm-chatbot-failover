from typing import Optional

from pydantic import BaseModel, Field, field_validator


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=4000)
    system_prompt: Optional[str] = Field(default=None, max_length=4000)

    @field_validator("message", mode="before")
    @classmethod
    def _strip_message(cls, v):
        # Length rules apply after trimming whitespace.
        return v.strip() if isinstance(v, str) else v

    @field_validator("session_id", mode="before")
    @classmethod
    def _strip_session(cls, v):
        return v.strip() if isinstance(v, str) else v
