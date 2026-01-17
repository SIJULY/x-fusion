# main.py
import logging
import asyncio
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from nicegui import ui, app
from fastapi import Request
from fastapi.responses import RedirectResponse

# 1. 导入核心模块
import core.state as state
from core.storage import init_data
from core.config import DATA_DIR

# 2. 导入业务服务
from services.jobs import start_scheduler
from api import register_api_routes

# 3. 导入 UI 组件与页面
from ui.assets import COMMON_HEAD_HTML
from ui.layout import init_layout
from ui.pages.login import login_page
from ui.pages.status import status_page_router
from ui.pages.router import route_to

# ================= 配置日志 =================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger("Main")

# ================= 初始化数据 =================
init_data()

# ================= 注册 API 路由 =================
register_api_routes(app)

# ================= ✨✨✨ [核心修复] 静态文件绝对路径 ✨✨✨ =================
# 获取 main.py 所在的绝对目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, 'static')

# 强制检查目录是否存在
if not os.path.exists(STATIC_DIR):
    logger.error(f"❌ 静态目录不存在: {STATIC_DIR}")
else:
    logger.info(f"📂 静态资源目录: {STATIC_DIR}")
    app.add_static_files('/static', STATIC_DIR)


# ================= 生命周期管理 =================
async def startup():
    state.PROCESS_POOL = ProcessPoolExecutor(max_workers=4)
    logger.info("🚀 进程池已启动")
    await start_scheduler()


async def shutdown():
    if state.PROCESS_POOL:
        state.PROCESS_POOL.shutdown(wait=False)
    logger.info("👋 系统已关闭")


app.on_startup(startup)
app.on_shutdown(shutdown)


# ================= 辅助函数 =================
def check_auth():
    if not app.storage.user.get('authenticated', False):
        return False
    current_ver = state.ADMIN_CONFIG.get('session_version', 'init')
    user_ver = app.storage.user.get('session_version', '')
    return current_ver == user_ver


# ================= 页面路由定义 =================
@ui.page('/login')
def route_login():
    login_page()


@ui.page('/status')
async def route_status(request: Request):
    await status_page_router(request)


@ui.page('/')
async def route_index(request: Request):
    if not check_auth():
        return RedirectResponse('/login')

    ui.add_head_html(COMMON_HEAD_HTML)
    # 注入JS变量，防止地图加载时变量未定义
    ui.add_body_html('<script>window.DASHBOARD_DATA = []; window.cachedWorldJson = null;</script>')

    client_ip = request.headers.get("X-Forwarded-For", request.client.host).split(',')[0].strip()

    init_layout(client_ip)
    ui.timer(0, lambda: route_to('DASHBOARD'), once=True)


# ================= 启动入口 =================
if __name__ in {"__main__", "__mp_main__"}:
    print(f"🚀 X-Fusion Panel 正在启动...")
    print(f"📂 数据目录: {DATA_DIR}")

    ui.run(
        title='X-Fusion Panel',
        host='0.0.0.0',
        port=8080,
        language='zh-CN',
        storage_secret='sijuly_secret_key_change_this',
        reload=False,
        favicon='🚀',
        reconnect_timeout=10.0,
    )