"""Domain extension point: observe the existing runtimes, never copy their policy."""

from app.application.services.durable_tasks import DurableTasks
from app.infrastructure.agent.delivery import AgentDelivery
from app.infrastructure.messaging.delivery_handlers import get_delivery_handlers


def get_delivery_observers(sessions):
    return {
        "tasks": DurableTasks(sessions, handlers=get_delivery_handlers()),
        "agent": AgentDelivery(sessions),
    }
