import json
import socket
import re
import time
import requests
import asyncio
from nicegui import run
from fastapi import APIRouter, Response, Request
from urllib.parse import urlparse

from app.core.state import (
    SERVERS_CACHE, SUBS_CACHE, NODES_DATA, ADMIN_CONFIG,
    PROBE_DATA_CACHE, PING_TREND_CACHE
)
from app.core.config import AUTO_REGISTER_SECRET, AUTO_COUNTRY_MAP
from app.core.data_manager import save_servers
from app.services.server_ops import force_geoip_naming_task, smart_detect_ssh_user_task
from app.utils.common import safe_base64, decode_base64_safe
from app.utils.parsers import generate_node_link
from app.ui.pages.dashboard import calculate_dashboard_data  # 引用计算逻辑

router = APIRouter()


# ================= 探针数据上报 =================
@router.post('/api/probe/push')
async def probe_push_data(request: Request):
    try:
        data = await request.json()
        token = data.get('token')
        server_url = data.get('server_url')

        correct_token = ADMIN_CONFIG.get('probe_token')
        if not token or token != correct_token: return Response("Invalid Token", 403)

        target_server = next((s for s in SERVERS_CACHE if s['url'] == server_url), None)
        if not target_server:
            try:
                push_ip = server_url.split('://')[-1].split(':')[0]
                for s in SERVERS_CACHE:
                    if s['url'].split('://')[-1].split(':')[0] == push_ip:
                        target_server = s;
                        break
            except:
                pass

        if target_server:
            if not target_server.get('probe_installed'): target_server['probe_installed'] = True

            data['status'] = 'online'
            data['last_updated'] = time.time()
            PROBE_DATA_CACHE[target_server['url']] = data

            # 记录历史趋势 (简易版)
            url = target_server['url']
            if url not in PING_TREND_CACHE: PING_TREND_CACHE[url] = []

            pings = data.get('pings', {})
            ct, cu, cm = pings.get('电信', 0), pings.get('联通', 0), pings.get('移动', 0)

            # 防抖逻辑 (1分钟记录一次)
            should_record = True
            if PING_TREND_CACHE[url]:
                if time.time() - PING_TREND_CACHE[url][-1]['ts'] < 60: should_record = False

            if should_record:
                import datetime
                time_str = datetime.datetime.now().strftime('%m/%d %H:%M')
                PING_TREND_CACHE[url].append({'ts': time.time(), 'time_str': time_str, 'ct': ct, 'cu': cu, 'cm': cm})
                if len(PING_TREND_CACHE[url]) > 1000: PING_TREND_CACHE[url] = PING_TREND_CACHE[url][-1000:]

        return Response("OK", 200)
    except Exception:
        return Response("Error", 500)


# ================= 订阅接口 =================
@router.get('/sub/{token}')
async def sub_handler(token: str):
    sub = next((s for s in SUBS_CACHE if s['token'] == token), None)
    if not sub: return Response("Invalid Token", 404)
    links = []

    for srv in SERVERS_CACHE:
        panel_nodes = NODES_DATA.get(srv['url'], []) or []
        custom_nodes = srv.get('custom_nodes', []) or []
        all_nodes = panel_nodes + custom_nodes
        if not all_nodes: continue

        raw_url = srv['url']
        try:
            if '://' not in raw_url: raw_url = f'http://{raw_url}'
            host = urlparse(raw_url).hostname or raw_url.split('://')[-1].split(':')[0]
        except:
            host = raw_url

        sub_nodes_set = set(sub.get('nodes', []))
        for n in all_nodes:
            if f"{srv['url']}|{n['id']}" in sub_nodes_set:
                if n.get('_raw_link'):
                    links.append(n['_raw_link'])
                else:
                    l = generate_node_link(n, host)
                    if l: links.append(l)

    return Response(safe_base64("\n".join(links)), media_type="text/plain; charset=utf-8")


# ================= 自动注册 =================
@router.post('/api/auto_register_node')
async def auto_register_node(request: Request):
    try:
        data = await request.json()
        if data.get('secret') != AUTO_REGISTER_SECRET:
            return Response(json.dumps({"success": False, "msg": "密钥错误"}), status_code=403)

        ip = data.get('ip')
        port = data.get('port')
        username = data.get('username')
        password = data.get('password')
        alias = data.get('alias', f'Auto-{ip}')
        ssh_port = data.get('ssh_port', 22)

        if not all([ip, port, username, password]):
            return Response(json.dumps({"success": False, "msg": "参数不完整"}), status_code=400)

        target_url = f"http://{ip}:{port}"

        new_server_config = {
            'name': alias, 'group': '自动注册', 'url': target_url,
            'user': username, 'pass': password, 'prefix': '',
            'ssh_port': ssh_port, 'ssh_auth_type': '全局密钥', 'ssh_user': 'detecting...',
            'probe_installed': False
        }

        existing_index = -1
        for idx, srv in enumerate(SERVERS_CACHE):
            if srv['url'].replace('http://', '') == target_url.replace('http://', ''):
                existing_index = idx;
                break

        if existing_index != -1:
            SERVERS_CACHE[existing_index].update(new_server_config)
            ref = SERVERS_CACHE[existing_index]
        else:
            SERVERS_CACHE.append(new_server_config)
            ref = new_server_config

        await save_servers()
        asyncio.create_task(force_geoip_naming_task(ref))
        asyncio.create_task(smart_detect_ssh_user_task(ref))

        return Response(json.dumps({"success": True, "msg": "注册成功"}), status_code=200)
    except Exception as e:
        return Response(json.dumps({"success": False, "msg": str(e)}), status_code=500)


# ================= 仪表盘轮询数据 =================
@router.get('/api/dashboard/live_data')
def get_dashboard_live_data_api():
    # 注意：这里需要 app.ui.pages.dashboard 中有 calculate_dashboard_data
    # 由于 Python 模块加载机制，需要在顶部 import
    data = calculate_dashboard_data()
    return data if data else {"error": "Calculation failed"}