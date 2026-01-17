import asyncio
import time
import logging
import random
import requests
from concurrent.futures import ProcessPoolExecutor
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# 引入核心模块
import app.core.state as state
from app.core.config import AUTO_COUNTRY_MAP
from app.core.data_manager import save_admin_config, save_servers, save_nodes_cache
from app.services.xui_client import fetch_inbounds_safe
from app.services.probe import get_server_status
from app.utils.geo_ip import fetch_geo_from_ip, get_flag_for_country

# 引入 UI 刷新引用 (为了通知前端更新)
from app.ui.components.sidebar import render_sidebar_content
from app.ui.pages.dashboard import load_dashboard_stats

logger = logging.getLogger("Scheduler")
scheduler = AsyncIOScheduler()

# ================= 辅助：Telegram 通知 =================
# 报警缓存
ALERT_CACHE = {}
FAILURE_COUNTS = {}


async def send_telegram_message(text):
    token = state.ADMIN_CONFIG.get('tg_bot_token')
    chat_id = state.ADMIN_CONFIG.get('tg_chat_id')
    if not token or not chat_id: return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}

    def _req():
        try:
            requests.post(url, json=payload, timeout=5)
        except Exception as e:
            logger.error(f"TG Error: {e}")

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _req)


# ================= 任务 1: 流量同步 (断点续传版) =================
async def job_sync_all_traffic():
    logger.info("🕒 [智能同步] 检查同步任务进度...")
    TARGET_DURATION = 84600  # 23.5 小时

    start_ts = state.ADMIN_CONFIG.get('sync_job_start', 0)
    current_idx = state.ADMIN_CONFIG.get('sync_job_index', 0)
    now = time.time()

    # 重置逻辑
    if (now - start_ts > 86400) or start_ts == 0 or current_idx >= len(state.SERVERS_CACHE):
        logger.info("🔄 [智能同步] 启动新一轮 24h 周期任务")
        start_ts = now;
        current_idx = 0
        state.ADMIN_CONFIG['sync_job_start'] = start_ts
        state.ADMIN_CONFIG['sync_job_index'] = 0
        await save_admin_config()
    else:
        logger.info(f"♻️ [智能同步] 恢复进度: 第 {current_idx + 1} 台")

    i = current_idx
    while True:
        current_total = len(state.SERVERS_CACHE)
        if i >= current_total: break

        try:
            server = state.SERVERS_CACHE[i]
        except:
            break

        loop_step_start = time.time()
        try:
            await fetch_inbounds_safe(server, force_refresh=True, sync_name=False)
            state.ADMIN_CONFIG['sync_job_index'] = i + 1
            await save_admin_config()

            # 动态休眠
            remaining = current_total - (i + 1)
            if remaining > 0:
                elapsed = time.time() - start_ts
                time_left = TARGET_DURATION - elapsed
                if time_left <= 0:
                    sleep_sec = 1
                else:
                    base = time_left / remaining
                    sleep_sec = max(1, base * random.uniform(0.9, 1.1) - (time.time() - loop_step_start))

                await asyncio.sleep(sleep_sec)
        except Exception as e:
            logger.warning(f"⚠️ 同步异常: {server.get('name')} - {e}")
            await asyncio.sleep(60)

        i += 1

    await save_nodes_cache()
    # 尝试刷新 UI
    try:
        await load_dashboard_stats()
    except:
        pass


# ================= 任务 2: 探针监控 & 报警 =================
async def job_monitor_status():
    sema = asyncio.Semaphore(50)
    FAILURE_THRESHOLD = 3
    current_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

    async def _check(srv):
        if not srv.get('probe_installed', False): return

        async with sema:
            await asyncio.sleep(0.01)
            res = await get_server_status(srv)
            name = srv.get('name', 'Unknown')
            url = srv['url']

            if not state.ADMIN_CONFIG.get('tg_bot_token'): return

            display_ip = url.split('://')[-1].split(':')[0]
            is_online = (isinstance(res, dict) and res.get('status') == 'online')

            if is_online:
                FAILURE_COUNTS[url] = 0
                if ALERT_CACHE.get(url) == 'offline':
                    msg = f"🟢 **恢复上线**\n🖥️ `{name}`\n🔗 `{display_ip}`\n🕒 `{current_time}`"
                    asyncio.create_task(send_telegram_message(msg))
                    ALERT_CACHE[url] = 'online'
            else:
                cnt = FAILURE_COUNTS.get(url, 0) + 1
                FAILURE_COUNTS[url] = cnt
                if cnt >= FAILURE_THRESHOLD:
                    if ALERT_CACHE.get(url) != 'offline':
                        msg = f"🔴 **离线报警**\n🖥️ `{name}`\n🔗 `{display_ip}`\n🕒 `{current_time}`"
                        asyncio.create_task(send_telegram_message(msg))
                        ALERT_CACHE[url] = 'offline'

    tasks = [_check(s) for s in state.SERVERS_CACHE]
    await asyncio.gather(*tasks)


# ================= 任务 3: GeoIP 修正 =================
async def job_check_geo_ip():
    logger.info("🌍 [定时任务] 检查 GeoIP...")
    data_changed = False
    known_flags = []
    for v in AUTO_COUNTRY_MAP.values():
        icon = v.split(' ')[0]
        if icon and icon not in known_flags: known_flags.append(icon)

    for s in state.SERVERS_CACHE:
        old_name = s.get('name', '')
        new_name = old_name

        # 清洗白旗
        if new_name.startswith('🏳️ ') and len(new_name) > 2:
            new_name = new_name.replace('🏳️', '').strip()

        has_flag = any(f in new_name for f in known_flags)
        if not has_flag:
            try:
                geo = await fetch_geo_from_ip(s['url'])  # 这里不需要 run.io_bound，因为 fetch_geo 内部已经处理或它是同步的
                # 注意：如果 fetch_geo_from_ip 是同步函数，这里需要 run_in_executor
                # 在之前的 utils/geo_ip.py 中 fetch_geo_from_ip 是同步的
                # 所以我们可以在这里不做修改，因为它耗时较短，或者优化为异步
                if geo:
                    s['lat'], s['lon'], s['_detected_region'] = geo
                    from app.utils.geo_ip import get_flag_for_country
                    flag = get_flag_for_country(geo[2]).split(' ')[0]
                    if flag and flag not in new_name:
                        new_name = f"{flag} {new_name}"
            except:
                pass

        if new_name != old_name:
            s['name'] = new_name
            data_changed = True

    if data_changed:
        await save_servers()
        try:
            render_sidebar_content.refresh()
        except:
            pass


# ================= 启动与关闭 =================

async def start_scheduler_service():
    """初始化进程池并启动定时任务"""
    # 1. 初始化进程池
    state.PROCESS_POOL = ProcessPoolExecutor(max_workers=4)
    logger.info("🚀 进程池已启动 (ProcessPoolExecutor)")

    # 2. 添加任务
    # 流量同步 (每24小时，但代码内部有循环逻辑，这里仅作为触发器)
    scheduler.add_job(job_sync_all_traffic, 'interval', hours=24, id='traffic_sync', replace_existing=True)

    # 状态监控 (每120秒)
    scheduler.add_job(job_monitor_status, 'interval', seconds=120, id='status_monitor', replace_existing=True)

    scheduler.start()
    logger.info("🕒 定时任务已启动")

    # 3. 立即触发一次初始化
    asyncio.create_task(job_sync_all_traffic())
    asyncio.create_task(job_check_geo_ip())

    # 延迟初始化报警缓存
    async def init_alert():
        await asyncio.sleep(5)
        if state.ADMIN_CONFIG.get('tg_bot_token'):
            await job_monitor_status()

    asyncio.create_task(init_alert())


def shutdown_scheduler_service():
    """关闭资源"""
    if state.PROCESS_POOL:
        state.PROCESS_POOL.shutdown(wait=False)
    if scheduler.running:
        scheduler.shutdown(wait=False)