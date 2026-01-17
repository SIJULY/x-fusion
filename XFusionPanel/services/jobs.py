# services/jobs.py
import asyncio
import time
import requests
import random
import logging
from collections import Counter
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from core.state import (
    SERVERS_CACHE, NODES_DATA, PROBE_DATA_CACHE,
    ADMIN_CONFIG, DASHBOARD_REFS
)
from core.storage import save_admin_config, save_nodes_cache, save_servers
from services.xui_api import fetch_inbounds_safe
from services.geoip import detect_country_group, job_check_geo_ip

logger = logging.getLogger("Services.Jobs")

# ================= TG 报警 =================
ALERT_CACHE = {}
FAILURE_COUNTS = {}


async def send_telegram_message(text):
    token = ADMIN_CONFIG.get('tg_bot_token')
    chat_id = ADMIN_CONFIG.get('tg_chat_id')
    if not token or not chat_id: return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}, timeout=5)
    except:
        pass


# ================= 状态监控任务 =================
async def get_server_status(server_conf):
    """获取单台服务器状态 (优先缓存)"""
    url = server_conf['url']
    # 探针或缓存优先
    if server_conf.get('probe_installed') or url in PROBE_DATA_CACHE:
        cache = PROBE_DATA_CACHE.get(url)
        if cache and (time.time() - cache.get('last_updated', 0) < 15):
            return cache
    return {'status': 'offline', 'msg': '未安装探针'}


async def job_monitor_status():
    """
    每2分钟执行：检查探针在线状态并报警
    """
    sema = asyncio.Semaphore(50)
    current_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

    async def _check(srv):
        if not srv.get('probe_installed'): return

        async with sema:
            res = await get_server_status(srv)
            is_online = (res.get('status') == 'online')
            url = srv['url']
            name = srv.get('name', 'Unknown')

            if is_online:
                FAILURE_COUNTS[url] = 0
                if ALERT_CACHE.get(url) == 'offline':
                    asyncio.create_task(send_telegram_message(f"🟢 **恢复**: {name} 已上线\n🕒 {current_time}"))
                    ALERT_CACHE[url] = 'online'
            else:
                FAILURE_COUNTS[url] = FAILURE_COUNTS.get(url, 0) + 1
                if FAILURE_COUNTS[url] >= 3 and ALERT_CACHE.get(url) != 'offline':
                    asyncio.create_task(send_telegram_message(f"🔴 **报警**: {name} 离线\n🕒 {current_time}"))
                    ALERT_CACHE[url] = 'offline'

    tasks = [_check(s) for s in SERVERS_CACHE]
    await asyncio.gather(*tasks)


# ================= 流量同步任务 (仅 API 模式) =================
async def job_sync_all_traffic():
    """
    每24小时执行：轮询 API 机器更新流量
    """
    logger.info("🕒 [同步任务] 检查流量同步进度...")
    start_ts = ADMIN_CONFIG.get('sync_job_start', 0)
    current_idx = ADMIN_CONFIG.get('sync_job_index', 0)
    now = time.time()

    # 重置逻辑
    if (now - start_ts > 86400) or start_ts == 0 or current_idx >= len(SERVERS_CACHE):
        start_ts = now;
        current_idx = 0
        ADMIN_CONFIG.update({'sync_job_start': start_ts, 'sync_job_index': 0})
        await save_admin_config()

    i = current_idx
    while i < len(SERVERS_CACHE):
        server = SERVERS_CACHE[i]

        # 跳过探针机器 (它们会自动推送)
        if server.get('probe_installed'):
            i += 1
            continue

        try:
            await fetch_inbounds_safe(server, force_refresh=True)
            # 动态休眠防封
            await asyncio.sleep(random.uniform(1, 2))
        except:
            pass

        i += 1
        # 保存进度
        if i % 5 == 0:
            ADMIN_CONFIG['sync_job_index'] = i
            await save_admin_config()

    await save_nodes_cache()
    logger.info("✅ [同步任务] 本轮完成")


# ================= 仪表盘数据计算 (核心) =================
def calculate_dashboard_data():
    """计算仪表盘所需的统计数据 (Server, Nodes, Traffic, Charts)"""
    try:
        total_srv = len(SERVERS_CACHE)
        online_srv = 0
        total_nodes = 0
        total_traffic = 0

        traffic_map = {}
        country_cnt = Counter()
        now_ts = time.time()

        for s in SERVERS_CACHE:
            nodes = NODES_DATA.get(s['url'], []) or []
            probe = PROBE_DATA_CACHE.get(s['url'])
            custom = s.get('custom_nodes', [])

            # 1. 区域统计
            c_name = detect_country_group(s.get('name', ''), s) or "🏳️ 未知"
            country_cnt[c_name] += 1

            # 2. 流量统计 (优先探针)
            s_traffic = 0
            if s.get('probe_installed') and probe:
                s_traffic = probe.get('net_total_in', 0) + probe.get('net_total_out', 0)
            elif nodes:
                s_traffic = sum(n.get('up', 0) + n.get('down', 0) for n in nodes)

            total_traffic += s_traffic
            traffic_map[s.get('name')] = s_traffic

            # 3. 在线判定
            is_on = False
            if probe and (now_ts - probe.get('last_updated', 0) < 60):
                is_on = True
            elif nodes or s.get('_status') == 'online':
                is_on = True

            if is_on: online_srv += 1
            total_nodes += len(nodes) + len(custom)

        # 构建图表数据
        top_traffic = sorted(traffic_map.items(), key=lambda x: x[1], reverse=True)[:15]

        pie_data = []
        if len(country_cnt) > 5:
            top_5 = country_cnt.most_common(5)
            others = sum(country_cnt.values()) - sum(x[1] for x in top_5)
            pie_data = [{'name': f"{k} ({v})", 'value': v} for k, v in top_5]
            if others > 0: pie_data.append({'name': f"🏳️ 其他 ({others})", 'value': others})
        else:
            pie_data = [{'name': f"{k} ({v})", 'value': v} for k, v in country_cnt.items()]

        return {
            "servers": f"{online_srv}/{total_srv}",
            "nodes": str(total_nodes),
            "traffic": f"{total_traffic / 1024 ** 3:.2f} GB",
            "subs": str(len(ADMIN_CONFIG.get('subs', []))),  # 暂用
            "bar_chart": {"names": [x[0] for x in top_traffic],
                          "values": [round(x[1] / 1024 ** 3, 2) for x in top_traffic]},
            "pie_chart": pie_data
        }
    except:
        return None


async def refresh_dashboard_ui_trigger():
    """触发前端 UI 刷新 (通过 State 中的引用)"""
    data = calculate_dashboard_data()
    if not data: return

    # 简单的文本更新
    if DASHBOARD_REFS['servers']: DASHBOARD_REFS['servers'].set_text(data['servers'])
    if DASHBOARD_REFS['nodes']: DASHBOARD_REFS['nodes'].set_text(data['nodes'])
    if DASHBOARD_REFS['traffic']: DASHBOARD_REFS['traffic'].set_text(data['traffic'])

    # 图表更新需在 UI 线程中做，这里仅更新数据源
    if DASHBOARD_REFS['bar_chart']:
        DASHBOARD_REFS['bar_chart'].options['xAxis']['data'] = data['bar_chart']['names']
        DASHBOARD_REFS['bar_chart'].options['series'][0]['data'] = data['bar_chart']['values']
        DASHBOARD_REFS['bar_chart'].update()


# ================= 调度器启动 =================
scheduler = AsyncIOScheduler()


async def start_scheduler():
    from services.ping import PROCESS_POOL
    # 此处假设 PROCESS_POOL 已在 main.py 初始化

    scheduler.add_job(job_sync_all_traffic, 'interval', hours=24, id='traffic_sync')
    scheduler.add_job(job_monitor_status, 'interval', seconds=120, id='status_monitor')
    scheduler.add_job(job_check_geo_ip, 'interval', hours=1, id='geoip_check')

    scheduler.start()
    logger.info("🕒 调度任务已启动")

    # 开机立即运行一次
    asyncio.create_task(job_sync_all_traffic())