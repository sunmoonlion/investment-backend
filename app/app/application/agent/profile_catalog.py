from __future__ import annotations

from app.domain.agent.profiles import AgentProfile, EffectiveAgentConfig, MemoryPolicyConfig


class AgentProfileCatalog:
    def __init__(self, profiles: dict[str, AgentProfile]):
        self._profiles = profiles

    def resolve(self, key: str | None) -> EffectiveAgentConfig:
        profile_key = key or "default_research"
        profile = self._profiles.get(profile_key)
        if profile is None:
            raise ValueError(f"unknown agent_profile_key: {profile_key}")
        return EffectiveAgentConfig(
            profile_key=profile.key,
            profile_version=profile.version,
            system_prompt_id=profile.system_prompt_id,
            model_key=profile.model_key,
            allowed_tools=set(profile.allowed_tools),
            denied_tools=set(profile.denied_tools),
            memory_policy=profile.memory_policy,
            ragflow_binding_key=profile.ragflow_binding_key,
        )


def build_builtin_profile_catalog() -> AgentProfileCatalog:
    default_memory = MemoryPolicyConfig(
        key="session_window_default",
        max_messages=20,
        max_tokens=12000,
        summarize_strategy="none",
        long_term_write_policy="disabled",
    )
    focused_memory = MemoryPolicyConfig(
        key="focused_research_window",
        max_messages=12,
        max_tokens=8000,
        summarize_strategy="rolling_summary",
        long_term_write_policy="disabled",
    )
    return AgentProfileCatalog(
        {
            "default_research": AgentProfile(
                key="default_research",
                version=1,
                name="Default Research Agent",
                system_prompt_id="mooc_manus.default_research.v1",
                model_key="default_chat",
                allowed_tools={"search", "browser", "file_read", "file_write"},
                denied_tools={"shell"},
                memory_policy=default_memory,
            ),
            "literature_review": AgentProfile(
                key="literature_review",
                version=1,
                name="Literature Review Agent",
                system_prompt_id="mooc_manus.literature_review.v1",
                model_key="default_chat",
                allowed_tools={"search", "browser", "file_read"},
                denied_tools={"shell", "file_write"},
                memory_policy=focused_memory,
                ragflow_binding_key="read_only_retrieve",
            ),
        }
    )


builtin_profile_catalog = build_builtin_profile_catalog()
