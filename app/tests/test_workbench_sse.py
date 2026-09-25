import json

from app.interfaces.endpoints.workbench_routes import _session_sse_frame


def test_session_sse_uses_message_dispatch_and_keeps_domain_event_type() -> None:
    event = {
        "cursor": 42,
        "type": "turn/completed",
        "payload": {"status": "done"},
    }

    frame = _session_sse_frame(event)

    assert frame.startswith("id: 42\nevent: message\ndata: ")
    assert frame.endswith("\n\n")
    data_line = next(line.removeprefix("data: ") for line in frame.splitlines() if line.startswith("data: "))
    assert json.loads(data_line) == event
