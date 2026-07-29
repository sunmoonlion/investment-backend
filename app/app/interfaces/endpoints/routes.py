from fastapi import APIRouter, Depends

from app.interfaces.endpoints.agent_routes import router as agent_router
from app.interfaces.endpoints.auth_routes import router as auth_router
from app.interfaces.endpoints.tasks_routes import router as tasks_router
from app.interfaces.middleware.auth import require_research_admin

router = APIRouter()

router.include_router(auth_router)
router.include_router(
    tasks_router, dependencies=[Depends(require_research_admin)]
)
router.include_router(
    agent_router, dependencies=[Depends(require_research_admin)]
)
