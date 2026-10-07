from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

JudgeCategory = Literal["tone", "answered", "faithfulness", "privacy", "relevance"]


class JudgeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: JudgeCategory
    rubric: str = Field(min_length=10)
    reference: str | None = None
