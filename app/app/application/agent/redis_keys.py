from core.config import get_settings


def agent_redis_key_prefix() -> str:
    return get_settings().agent_redis_key_prefix.strip(":")


def session_lock_key(session_id: str) -> str:
    return f"{agent_redis_key_prefix()}:session:{session_id}:lock"


def session_events_channel(session_id: str) -> str:
    return f"{agent_redis_key_prefix()}:session:{session_id}:events"


def session_deltas_channel(session_id: str) -> str:
    return f"{agent_redis_key_prefix()}:session:{session_id}:deltas"
