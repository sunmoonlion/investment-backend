from __future__ import annotations

from app.domain.agent.llm import LLMRequest, LLMResponse, make_assistant_message


class DeterministicFakeLLM:
    async def complete(self, request: LLMRequest) -> LLMResponse:
        visible_messages = [message.content for message in request.context.messages]
        content = " | ".join(visible_messages) if visible_messages else "ok"
        return LLMResponse(
            message=make_assistant_message(
                f"fake:{request.context.prompt_id}:{content}",
                sequence_no=len(request.context.messages) + 1,
            ),
            model_key=request.model_key,
            prompt_id=request.context.prompt_id,
            usage={
                "input_messages": len(request.context.messages),
                "memory_refs": len(request.context.memory_refs),
                "evidence_refs": len(request.context.evidence_refs),
            },
        )
