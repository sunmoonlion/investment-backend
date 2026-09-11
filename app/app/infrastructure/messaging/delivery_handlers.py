"""Explicit domain extension point; templates do not invent business tasks."""

from app.application.services.durable_tasks import Handler


def get_delivery_handlers() -> dict[str, Handler]:
    return {}
