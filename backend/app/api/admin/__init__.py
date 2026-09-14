"""管理面路由聚合（W2 任务 1：auth + keys CRUD；W3 任务 1：models 定价 CRUD；
W4 任务 2：channels CRUD + dashboard + play 代理）。"""

from fastapi import APIRouter

from app.api.admin.auth import router as auth_router
from app.api.admin.channels import router as channels_router
from app.api.admin.dashboard import router as dashboard_router
from app.api.admin.keys import router as keys_router
from app.api.admin.models import router as models_router
from app.api.admin.play import router as play_router

router = APIRouter()
router.include_router(auth_router)
router.include_router(keys_router)
router.include_router(models_router)
router.include_router(channels_router)
router.include_router(dashboard_router)
router.include_router(play_router)