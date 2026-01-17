# main.py
import sys
import logging
import asyncio
from concurrent.futures import ProcessPoolExecutor
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from nicegui import ui, app
from fastapi import Request # ✨✨✨ 必须导入这个 ✨✨✨

import config
import state
import logic
import routes
import ui_layout

# 日志配置
sys.stdout.reconfigure(line_buffering=True)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S', force=True, handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger("XUI_Manager")

# 初始化数据
logic.init_data()

# 注册 API 路由
# 注意：routes 中的函数必须也有 type hint，已经在之前的 routes.py 中处理好了
app.add_api_route('/api/probe/push', routes.probe_push_data, methods=['POST'])
app.add_api_route('/sub/{token}', routes.sub_handler, methods=['GET'])
app.add_api_route('/sub/group/{group_b64}', routes.group_sub_handler, methods=['GET'])
app.add_api_route('/get/group/{target}/{group_b64}', routes.short_group_handler, methods=['GET'])
app.add_api_route('/get/sub/{target}/{token}', routes.short_sub_handler, methods=['GET'])
app.add_api_route('/api/probe/register', routes.probe_register, methods=['POST'])
app.add_api_route('/api/auto_register_node', routes.auto_register_node, methods=['POST'])
app.add_api_route('/api/dashboard/live_data', routes.get_dashboard_live_data, methods=['GET'])

# ================= 注册页面路由 (UI) =================
# ✨✨✨ 修复核心：必须加上 : Request 类型提示 ✨✨✨

@ui.page('/login')
def login_page(request: Request):
    return ui_layout.login_page(request)

@ui.page('/')
def main_page(request: Request):
    return ui_layout.main_page(request)

@ui.page('/status')
async def status_page(request: Request):
    return await ui_layout.status_page_router(request)

# 注册静态文件
app.add_static_files('/static', 'static')

# 启动序列
async def startup_sequence():
    state.PROCESS_POOL = ProcessPoolExecutor(max_workers=4)
    logger.info("🚀 进程池已启动")

    scheduler = AsyncIOScheduler()
    scheduler.add_job(logic.job_sync_all_traffic, 'interval', hours=24, id='traffic_sync', replace_existing=True)
    scheduler.add_job(logic.job_monitor_status, 'interval', seconds=120, id='status_monitor', replace_existing=True)
    scheduler.start()
    logger.info("🕒 定时任务已启动")

    asyncio.create_task(logic.job_sync_all_traffic())
    asyncio.create_task(logic.job_check_geo_ip())

app.on_startup(startup_sequence)
app.on_shutdown(lambda: state.PROCESS_POOL.shutdown(wait=False) if state.PROCESS_POOL else None)

if __name__ in {"__main__", "__mp_main__"}:
    ui.run(
        title='X-Fusion Panel',
        host='0.0.0.0',
        port=8080,
        language='zh-CN',
        storage_secret='sijuly_secret_key',
        reload=False,
        reconnect_timeout=600.0,
        ws_ping_interval=20,
        ws_ping_timeout=20,
        timeout_keep_alive=60
    )