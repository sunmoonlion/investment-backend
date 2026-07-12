from fastapi import APIRouter, Depends

from app.interfaces.endpoints.auth_routes import router as auth_router
from app.interfaces.endpoints.agent_routes import router as agent_router
from app.interfaces.middleware.auth import require_research_admin

router = APIRouter()

router.include_router(auth_router)
router.include_router(
    agent_router,
    dependencies=[Depends(require_research_admin)],
)

# Internal task routes remain unavailable until the service-token boundary is
# installed; an Admin session must never be accepted as a service credential.

# 在此注册其他业务模块路由
# from app.interfaces.endpoints.user_routes import router as user_router
# router.include_router(user_router)
