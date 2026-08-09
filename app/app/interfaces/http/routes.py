from fastapi import APIRouter

from app.interfaces.endpoints.agent_routes import router as agent_router
from app.interfaces.endpoints.pilot_runtime_routes import router as pilot_runtime_router
from app.interfaces.http.admin.auth import router as admin_auth_router
from app.interfaces.http.admin.diagnostics import router as admin_diagnostics_router
from app.interfaces.http.web.auth import router as web_auth_router
from app.interfaces.http.web.interactions import router as web_interactions_router

router = APIRouter()
router.include_router(admin_auth_router)
router.include_router(web_auth_router)
router.include_router(admin_diagnostics_router)
router.include_router(web_interactions_router)
router.include_router(agent_router)
router.include_router(pilot_runtime_router)
