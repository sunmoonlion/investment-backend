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


class ProjectPathInvalid(WorkbenchError):
    """项目的目录不合规则：越出工作区、带盘符、往上走。"""

    code = "project_path_invalid"
    http_status = 400


class ProjectExists(WorkbenchError):
    """同一台机器、同一个目录已经有一个项目。"""

    code = "project_exists"
    http_status = 409


class ProjectArchived(WorkbenchError):
    code = "project_archived"
    http_status = 409


class ProjectRequired(WorkbenchError):
    """工作与专家必须在项目里；不属于项目的聊天要先放进一个项目。"""

    code = "project_required"
    http_status = 409


class ProjectBusy(WorkbenchError):
    """这个项目里已经有一件事专家还在做：同一时刻只有一个（所有者 2026-09-29 定）。"""

    code = "project_busy"
    http_status = 409


class ProjectHeldByExpert(WorkbenchError):
    """专家在这个项目里干活期间，别的对话可以聊天，不可以工作（所有者 2026-09-29 定）。"""

    code = "project_held_by_expert"
    http_status = 409


class ConversationChangeRefused(WorkbenchError):
    """对话的种类、归属不能这样改：工作不转回聊天；已经归入项目的不能换项目。"""

    code = "conversation_change_refused"
    http_status = 409


class NoSandbox(WorkbenchError):
    """用户还没有可用的沙箱：先在设置里登记模型 key、拉起沙箱。"""

    code = "no_sandbox"
    http_status = 409


class EnvironmentOffline(WorkbenchError):
    """项目所在的机器不在线。专家要在上面干活，所以交不出去。"""

    code = "environment_offline"
    http_status = 409


class NoSuchExpert(WorkbenchError):
    """没有这位专家，或者它不给用户用。"""

    code = "no_such_expert"
    http_status = 404


class RecordsRefused(WorkbenchError):
    """专家读项目记录被拒：不是处理期间，或者要读的东西不在这个项目里。"""

    code = "records_refused"
    http_status = 403
