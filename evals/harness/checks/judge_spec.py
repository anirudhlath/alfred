from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

JudgeCategory = Literal["tone", "answered", "faithfulness", "privacy", "relevance"]
# One rule for every rubric the judge reads: a scenario's check and a calibration item.
Rubric = Annotated[str, Field(min_length=10)]


class JudgeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: JudgeCategory
    rubric: Rubric
    reference: str | None = None
