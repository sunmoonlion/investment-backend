from __future__ import annotations

from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage

from app.domain.agent.commands import CancelRunCommand
from app.domain.agent.message_upcaster import upcast_stored_message
from app.domain.agent.models import MessageRole, StoredMessage
from app.infrastructure.agent.message_mapper import (
    base_message_to_stored,
    stored_message_to_base,
)


def test_stored_message_upcaster_accepts_current_schema() -> None:
    message = upcast_stored_message(
        {
            "role": "user",
            "content": "hello",
            "sequence_no": 1,
            "schema_version": 1,
        }
    )

    assert message == StoredMessage(role=MessageRole.user, content="hello", sequence_no=1)


def test_langchain_message_mapping_stays_behind_infrastructure_adapter() -> None:
    stored = base_message_to_stored(HumanMessage(content="hello", id="m1"), sequence_no=1)

    assert stored.role == MessageRole.user
    assert stored.content == "hello"
    assert stored.message_id == "m1"

    restored = stored_message_to_base(stored)
    assert isinstance(restored, HumanMessage)
    assert restored.content == "hello"

    assistant = base_message_to_stored(AIMessage(content="hi"), sequence_no=2)
    assert assistant.role == MessageRole.assistant


def test_cancel_run_command_names_request_intent() -> None:
    command = CancelRunCommand(run_id="run-1", reason="user requested")

    assert command.run_id == "run-1"
    assert command.reason == "user requested"


def test_domain_and_application_import_boundaries() -> None:
    root = Path("app")
    forbidden_by_area = {
        "domain/agent": ("langgraph", "langchain_core"),
        "application/agent": ("langgraph",),
    }

    for area, forbidden_imports in forbidden_by_area.items():
        for path in (root / area).glob("*.py"):
            source = path.read_text(encoding="utf-8")
            for forbidden in forbidden_imports:
                assert forbidden not in source, f"{path} must not import {forbidden}"
