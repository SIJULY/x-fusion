# api/probe_api.py
import time
import json
import socket
import re
import asyncio
from fastapi import APIRouter, Request, Response
from nicegui import run

from core.state import (
    SERVERS_CACHE, PROBE_DATA_CACHE, NODES_DATA,
    ADMIN_CONFIG, IP_GEO_CACHE
)
from core.config import AUTO_REGISTER_SECRET
from core.storage import save_servers
from services.geoip import fetch_geo_from_ip, get_flag_for_country, force_geoip_naming_task, auto_prepend_flag
from services.ping import record_ping_history
from services.ssh_manager import smart_detect_ssh_user_task
from services.jobs import refresh_dashboard_ui_trigger

probe_router = APIRouter()


# --- 探针推送接口 ---
@probe_router.post('/api/probe/push')
async def probe_push_data(request: Request):
    try:
        data = await request.json()
        token = data.get('token')
        server_url = data.get('server_url')

        # 1. 校验 Token
        correct_token = ADMIN_CONFIG.get('probe_token')
        if not token or token != correct_token:
            return Response("Invalid Token", 403)

        # 2. 查找服务器 (精准匹配 -> IP匹配)
        target_server = next((s for s in SERVERS_CACHE if s['url'] == server_url), None)
        if not target_server:
            try:
                push_ip = server_url.split('://')[-1].split(':')[0]
                for s in SERVERS_CACHE:
                    cache_ip = s['url'].split('://')[-1].split(':')[0]
                    if cache_ip == push_ip:
                        target_server = s
                        break
            except:
                pass

        if target_server:
            # 激活探针状态
            if not target_server.get('probe_installed'):
                target_server['probe_installed'] = True

            # 3. 写入基础监控数据缓存
            data['status'] = 'online'
            data['last_updated'] = time.time()
            PROBE_DATA_CACHE[target_server['url']] = data

            # 4. 处理 X-UI 数据 & 自动命名
            if 'xui_data' in data and isinstance(data['xui_data'], list):
                raw_nodes = data['xui_data']
                parsed_nodes = []
                for n in raw_nodes:
                    try:
                        if isinstance(n.get('settings'), str):
                            n['settings'] = json.loads(n['settings'])
                        if isinstance(n.get('streamSettings'), str):
                            n['streamSettings'] = json.loads(n['streamSettings'])
                        parsed_nodes.append(n)
                    except:
                        parsed_nodes.append(n)

                NODES_DATA[target_server['url']] = parsed_nodes
                target_server['_status'] = 'online'

                # 自动同步名称逻辑 (当端口不通时依赖此逻辑)
                if parsed_nodes:
                    first_remark = parsed_nodes[0].get('remark', '').strip()
                    current_name = target_server.get('name', '').strip()
                    if first_remark and (first_remark not in current_name):
                        # 尝试添加国旗
                        new_name = await auto_prepend_flag(first_remark, target_server['url'])
                        if target_server['name'] != new_name:
                            target_server['name'] = new_name
                            await save_servers()

            # 记录历史
            record_ping_history(target_server['url'], data.get('pings', {}))

        return Response("OK", 200)
    except Exception as e:
        return Response("Error", 500)


# --- 自动注册接口 ---
@probe_router.post('/api/auto_register_node')
async def auto_register_node(request: Request):
    try:
        data = await request.json()

        # 安全校验
        secret = data.get('secret')
        if secret != AUTO_REGISTER_SECRET:
            return Response(json.dumps({"success": False, "msg": "Key Error"}), status_code=403,
                            media_type="application/json")

        ip = data.get('ip')
        port = data.get('port')
        username = data.get('username')
        password = data.get('password')
        alias = data.get('alias', f'Auto-{ip}')
        ssh_port = data.get('ssh_port', 22)

        if not all([ip, port, username, password]):
            return Response(json.dumps({"success": False, "msg": "Incomplete params"}), status_code=400,
                            media_type="application/json")

        target_url = f"http://{ip}:{port}"

        new_server_config = {
            'name': alias,
            'group': '默认分组',
            'url': target_url,
            'user': username,
            'pass': password,
            'prefix': '',
            'ssh_port': ssh_port,
            'ssh_auth_type': '全局密钥',
            'ssh_user': 'root',  # 初始值
            'probe_installed': False
        }

        # 查重逻辑
        existing_index = -1
        for idx, srv in enumerate(SERVERS_CACHE):
            if srv['url'] == target_url:
                existing_index = idx
                break

        if existing_index != -1:
            SERVERS_CACHE[existing_index].update(new_server_config)
            target_server_ref = SERVERS_CACHE[existing_index]
        else:
            SERVERS_CACHE.append(new_server_config)
            target_server_ref = new_server_config

        await save_servers()

        # 启动后台任务 (GeoIP 命名 + SSH 探测)
        asyncio.create_task(force_geoip_naming_task(target_server_ref))
        asyncio.create_task(smart_detect_ssh_user_task(target_server_ref))

        return Response(json.dumps({"success": True, "msg": "Registered"}), status_code=200,
                        media_type="application/json")

    except Exception as e:
        return Response(json.dumps({"success": False, "msg": str(e)}), status_code=500, media_type="application/json")