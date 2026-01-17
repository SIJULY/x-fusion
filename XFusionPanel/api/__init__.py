# api/__init__.py
from .probe_api import probe_router
from .sub_api import sub_router
from .dashboard_api import dashboard_router

def register_api_routes(fastapi_app):
    """
    注册所有 API 路由到 FastAPI 应用
    """
    fastapi_app.include_router(probe_router)
    fastapi_app.include_router(sub_router)
    fastapi_app.include_router(dashboard_router)