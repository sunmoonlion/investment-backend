from __future__ import annotations


class WorkbenchError(Exception):
    code = "workbench_error"
    http_status = 400

    def __init__(self, message: str, **details: object):
        super().__init__(message)
        self.message = message
        self.details = details


class NotFound(WorkbenchError):
    code = "not_found"
    http_status = 404


class Forbidden(WorkbenchError):
    code = "forbidden"
    http_status = 403


class WheelHeldByOther(WorkbenchError):
    """方向盘在对方手里：用户在 advisor 期间发 turn，或顾问在 user 期间发 turn（AT-06）。"""

    code = "wheel_held_by_other"
    http_status = 409


class IdempotencyConflict(WorkbenchError):
    """同键异摘要（AT-02）。"""

    code = "idempotency_conflict"
    http_status = 409


class StaleStateVersion(WorkbenchError):
    """比较交换失败：另一个入口先动了（AT-13）。"""

    code = "stale_state_version"
    http_status = 409


class InteractionRejected(WorkbenchError):
    """令牌重复、过期、异键、跨 Task、摘要不符（AT-07）。"""

    code = "interaction_rejected"
    http_status = 409


class BudgetExhausted(WorkbenchError):
    code = "budget_exhausted"
    http_status = 409


class RootOutsideWhitelist(WorkbenchError):
    code = "root_outside_whitelist"
    http_status = 400
