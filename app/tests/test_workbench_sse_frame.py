"""会话事件流的帧格式：浏览器 EventSource 的 onmessage 只收无名帧，所以帧里不能有 `event:` 行（KIND 07 实测踩过）。"""

from __future__ import annotations

import json

from app.interfaces.endpoints.workbench_routes import sse_frame


def test_frame_has_no_event_name_and_carries_type_in_data():
    ev = {
        "id": "e1",
        "cursor": 7,
        "kind": "turn",
        "type": "turn/completed",
        "payload": {"x": "中"},
    }
    frame = sse_frame(ev)
    lines = frame.split("\n")
    assert frame.endswith("\n\n")
    assert not any(line.startswith("event:") for line in lines)
    assert lines[0] == "id: 7"
    data = json.loads(lines[1].removeprefix("data: "))
    assert data["type"] == "turn/completed" and data["payload"]["x"] == "中"
