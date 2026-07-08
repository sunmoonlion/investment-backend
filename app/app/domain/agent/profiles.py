from __future__ import annotations

from pydantic import BaseModel, Field


class MemoryPolicyConfig(BaseModel):
    key: str
    max_messages: int = 20
    max_tokens: int = 12000
    summarize_strategy: str = "none"
    long_term_write_policy: str = "disabled"


class AgentProfile(BaseModel):
    key: str
    version: int = 1
    name: str
    system_prompt_id: str
    model_key: str
    allowed_tools: set[str] = Field(default_factory=set)
    denied_tools: set[str] = Field(default_factory=set)
    memory_policy: MemoryPolicyConfig
    ragflow_binding_key: str | None = None


class EffectiveAgentConfig(BaseModel):
    profile_key: str
    profile_version: int
    system_prompt_id: str
    model_key: str
    allowed_tools: set[str]
    denied_tools: set[str]
    memory_policy: MemoryPolicyConfig
    ragflow_binding_key: str | None = None

    def permits_tool(self, tool_key: str) -> bool:
        if tool_key in self.denied_tools:
            return False
        if not self.allowed_tools:
            return True
        return tool_key in self.allowed_tools
