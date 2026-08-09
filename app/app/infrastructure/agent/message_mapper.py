from __future__ import annotations

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from app.domain.agent.models import MessageRole, StoredMessage


def base_message_to_stored(message: BaseMessage, *, sequence_no: int) -> StoredMessage:
    content = (
        message.content if isinstance(message.content, str) else str(message.content)
    )
    if isinstance(message, HumanMessage):
        role = MessageRole.user
    elif isinstance(message, AIMessage):
        role = MessageRole.assistant
    elif isinstance(message, SystemMessage):
        role = MessageRole.system
    elif isinstance(message, ToolMessage):
        role = MessageRole.tool
    else:
        raise ValueError(
            f"unsupported LangChain message type: {type(message).__name__}"
        )

    return StoredMessage(
        role=role,
        content=content,
        sequence_no=sequence_no,
        message_id=str(message.id) if message.id else None,
        tool_call_id=getattr(message, "tool_call_id", None),
        metadata={"langchain_type": message.type},
    )


def stored_message_to_base(message: StoredMessage) -> BaseMessage:
    if message.role == MessageRole.user:
        return HumanMessage(content=message.content, id=message.message_id)
    if message.role == MessageRole.assistant:
        return AIMessage(content=message.content, id=message.message_id)
    if message.role == MessageRole.system:
        return SystemMessage(content=message.content, id=message.message_id)
    if message.role == MessageRole.tool:
        if not message.tool_call_id:
            raise ValueError("tool StoredMessage requires tool_call_id")
        return ToolMessage(
            content=message.content,
            tool_call_id=message.tool_call_id,
            id=message.message_id,
        )
    raise ValueError(f"unsupported StoredMessage role: {message.role}")
