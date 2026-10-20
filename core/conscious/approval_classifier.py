"""Reading the owner's reply to a pending confirmation as yes, no or neither.

One small model call on ``APPROVAL_MODEL``, built once per process: the prompt says what Alfred
asked to do (``approval-classifier``), the reply is the user prompt, and the answer is a
``Decision``. The engine runs a parked call only on ``"approve"``; a model that is missing,
gives up or is out of budget answers ``"other"``, so the call is left unconfirmed rather than
guessed at. A provider or transport failure propagates, so the request stays pending and is
retried (``docs/llm.md``, "Retries").
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Final

from pydantic import BaseModel
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.settings import ModelSettings

from core.conscious.approvals import Decision, summary_of
from shared.agents import instrumentation, role_headers
from shared.config import DEFAULT_SYSTEM2_TIMEOUT_S
from shared.llm_roles import APPROVAL
from shared.prompts.link import linked_prompts
from shared.prompts.names import APPROVAL_CLASSIFIER

if TYPE_CHECKING:
    from pydantic_ai.capabilities import AbstractCapability
    from pydantic_ai.models import Model
    from pydantic_ai.output import OutputSpec
    from pydantic_ai_harness.spend import SpendLimits

    from bus.schemas.events import ParkedCall
    from shared.prompts.store import PromptStore

logger = logging.getLogger(__name__)

AGENT_NAME: Final = "approval-classifier"
# A verdict is one word in a JSON object; the cap only stops a runaway answer.
MAX_TOKENS: Final = 200
# One more try when the output fails validation; after that the reply counts as "other".
OUTPUT_RETRIES: Final = 1


class ApprovalVerdict(BaseModel):
    """How the owner's reply answers the pending confirmation."""

    decision: Decision


def _output(model: Model) -> OutputSpec[ApprovalVerdict]:
    """JSON-schema output where the model supports it, an output tool where it does not."""
    if model.profile.get("supports_json_schema_output", False):
        return NativeOutput(ApprovalVerdict)
    return ApprovalVerdict


class ApprovalClassifier:
    """Classifies a reply against the calls a parked run is waiting on.

    *model* is None when ``APPROVAL_MODEL`` resolved to nothing at startup; every reply is
    then ``"other"``. *spend* is the process's daily budget, which the agent carries.
    """

    def __init__(
        self, *, model: Model | None, prompts: PromptStore, spend: SpendLimits[Any]
    ) -> None:
        self._model = model
        self._prompts = prompts
        self._agent: Agent[None, ApprovalVerdict] | None = None
        if model is not None:
            capabilities: list[AbstractCapability[None]] = [instrumentation(), spend]
            self._agent = Agent(
                None,
                output_type=_output(model),
                name=AGENT_NAME,
                retries=OUTPUT_RETRIES,
                model_settings=ModelSettings(extra_headers=role_headers(APPROVAL)),
                capabilities=capabilities,
                defer_model_check=True,
            )

    async def classify(self, calls: list[ParkedCall], reply: str) -> Decision:
        """The decision *reply* makes about *calls*; ``"other"`` when no verdict is reached.

        Raises:
            ValueError: the prompt store does not know the prompt (a bug in the caller).
        """
        if self._agent is None:
            return "other"
        instructions = await self._prompts.get(APPROVAL_CLASSIFIER, actions=summary_of(calls))
        try:
            with linked_prompts(instructions.ref):
                result = await self._agent.run(
                    reply,
                    instructions=instructions.text,
                    model=self._model,
                    model_settings=ModelSettings(
                        max_tokens=MAX_TOKENS, timeout=DEFAULT_SYSTEM2_TIMEOUT_S
                    ),
                )
        except (UnexpectedModelBehavior, UsageLimitExceeded) as exc:
            # UsageLimitExceeded covers the daily budget (SpendLimitExceeded) too.
            logger.warning(
                "Approval classifier gave no verdict, so the reply counts as 'other': %s", exc
            )
            return "other"
        return result.output.decision
