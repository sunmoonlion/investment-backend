from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class AgentErrorCode(StrEnum):
    budget_exceeded = "budget_exceeded"
    invalid_state = "invalid_state"
    tool_denied = "tool_denied"
    tool_failed = "tool_failed"
    provider_failed = "provider_failed"
    unknown = "unknown"


class AgentError(BaseModel):
    code: AgentErrorCode
    message: str
    retryable: bool = False
    details: dict[str, str] = Field(default_factory=dict)

    def as_run_error(self) -> dict[str, object]:
        return {
            "code": self.code.value,
            "message": self.message,
            "retryable": self.retryable,
            "details": self.details,
        }


class RunBudget(BaseModel):
    max_steps: int = Field(default=20, ge=1)
    max_tool_calls: int = Field(default=20, ge=0)
    max_llm_calls: int = Field(default=20, ge=0)
    max_input_tokens: int = Field(default=120000, ge=1)
    steps_used: int = Field(default=0, ge=0)
    tool_calls_used: int = Field(default=0, ge=0)
    llm_calls_used: int = Field(default=0, ge=0)
    input_tokens_used: int = Field(default=0, ge=0)

    def check(self) -> AgentError | None:
        if self.steps_used > self.max_steps:
            return AgentError(
                code=AgentErrorCode.budget_exceeded,
                message="run step budget exceeded",
                details={"budget": "steps"},
            )
        if self.tool_calls_used > self.max_tool_calls:
            return AgentError(
                code=AgentErrorCode.budget_exceeded,
                message="run tool-call budget exceeded",
                details={"budget": "tool_calls"},
            )
        if self.llm_calls_used > self.max_llm_calls:
            return AgentError(
                code=AgentErrorCode.budget_exceeded,
                message="run llm-call budget exceeded",
                details={"budget": "llm_calls"},
            )
        if self.input_tokens_used > self.max_input_tokens:
            return AgentError(
                code=AgentErrorCode.budget_exceeded,
                message="run input-token budget exceeded",
                details={"budget": "input_tokens"},
            )
        return None

    def consume_step(self, amount: int = 1) -> "RunBudget":
        return self.model_copy(update={"steps_used": self.steps_used + amount})

    def consume_tool_call(self, amount: int = 1) -> "RunBudget":
        return self.model_copy(update={"tool_calls_used": self.tool_calls_used + amount})

    def consume_llm_call(self, amount: int = 1) -> "RunBudget":
        return self.model_copy(update={"llm_calls_used": self.llm_calls_used + amount})

    def consume_input_tokens(self, amount: int) -> "RunBudget":
        return self.model_copy(update={"input_tokens_used": self.input_tokens_used + amount})
