"""Explicit domain extension point; templates do not invent business tasks."""

from app.infrastructure.messaging.durable_tasks import Handler


def get_delivery_handlers() -> dict[str, Handler]:
    return {}
