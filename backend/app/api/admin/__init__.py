"""管理面路由聚合（W2 任务 1：auth + keys CRUD；W3 任务 1：models 定价 CRUD）。"""

from fastapi import APIRouter

from app.api.admin.auth import router as auth_router
from app.api.admin.keys import router as keys_router
from app.api.admin.models import router as models_router

router = APIRouter()
router.include_router(auth_router)
router.include_router(keys_router)
router.include_router(models_router)