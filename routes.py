# routes.py
import json
import asyncio
import socket
import re
import time
import requests
import logging
from urllib.parse import urlparse, quote
from fastapi import Request, Response

import config
import state
import logic
import utils

logger = logging.getLogger("XUI_Manager")


# ================= 探针数据被动接收接口 (最终修复版：防双重国旗) =================
async def probe_push_data(request: Request):
    try:
        data = await request.json()
        token = data.get('token')
        server_url = data.get('server_url')

        # 1. 校验 Token
        correct_token = state.ADMIN_CONFIG.get('probe_token')
        if not token or token != correct_token:
            return Response("Invalid Token", 403)

        # 2. 查找服务器 (精准匹配 -> IP匹配)
        target_server = next((s for s in state.SERVERS_CACHE if s['url'] == server_url), None)
        if not target_server:
            try:
                push_ip = server_url.split('://')[-1].split(':')[0]
                for s in state.SERVERS_CACHE:
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
            state.PROBE_DATA_CACHE[target_server['url']] = data

            # ✨✨✨ 核心逻辑：处理 X-UI 数据 & 自动命名 ✨✨✨
            if 'xui_data' in data and isinstance(data['xui_data'], list):
                # 解析节点
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

                # 更新节点缓存
                state.NODES_DATA[target_server['url']] = parsed_nodes
                target_server['_status'] = 'online'

                # 🟢 [新增补充]：自动同步名称逻辑 (当端口不通时依赖此逻辑)
                # 只有当有节点，且当前名字看起来像默认IP时，才尝试修改
                if parsed_nodes:
                    first_remark = parsed_nodes[0].get('remark', '').strip()
                    current_name = target_server.get('name', '').strip()

                    # 简单的判断：如果名字里没有这个备注
                    if first_remark and (first_remark not in current_name):

                        # ✨✨✨ [修复]：先检查备注里是否自带了国旗 ✨✨✨
                        has_own_flag = False
                        # 遍历全局配置中的所有已知国旗
                        for v in config.AUTO_COUNTRY_MAP.values():
                            known_flag = v.split(' ')[0]  # 提取 "🇺🇸"
                            if known_flag in first_remark:
                                has_own_flag = True
                                break

                        if has_own_flag:
                            # 情况 A：备注自带国旗 (如 "Oracle|🇺🇸凤凰城") -> 直接用，不加前缀
                            new_name_candidate = first_remark
                        else:
                            # 情况 B：备注没国旗 -> 尝试继承旧国旗或查询 GeoIP 加上
                            flag = "🏳️"
                            # 1. 尝试沿用当前名字里的国旗
                            if ' ' in current_name:
                                parts = current_name.split(' ', 1)
                                if len(parts[0]) < 10:
                                    flag = parts[0]
                            else:
                                # 2. 尝试重新获取国旗 (GeoIP)
                                try:
                                    ip_key = target_server['url'].split('://')[-1].split(':')[0]
                                    geo_info = state.IP_GEO_CACHE.get(ip_key)
                                    if geo_info:
                                        flag = utils.get_flag_for_country(geo_info[2]).split(' ')[0]
                                except:
                                    pass

                            new_name_candidate = f"{flag} {first_remark}"

                        # 执行改名并保存
                        if target_server['name'] != new_name_candidate:
                            target_server['name'] = new_name_candidate
                            asyncio.create_task(logic.save_servers())
                            logger.info(f"🏷️ [探针同步] 根据节点备注自动改名: {new_name_candidate}")

            # 记录历史
            logic.record_ping_history(target_server['url'], data.get('pings', {}))

        return Response("OK", 200)
    except Exception as e:
        return Response("Error", 500)


# =================  订阅接口：严格遵循自定义顺序 =================
async def sub_handler(token: str, request: Request):
    sub = next((s for s in state.SUBS_CACHE if s['token'] == token), None)
    if not sub: return Response("Invalid Token", 404)

    links = []

    # 1. 构建快速查找字典 (Map)
    # 格式: { 'url|id': (node_data, server_host) }
    node_lookup = {}

    for srv in state.SERVERS_CACHE:
        # 获取 Host
        raw_url = srv['url']
        try:
            if '://' not in raw_url: raw_url = f'http://{raw_url}'
            parsed = urlparse(raw_url)
            host = parsed.hostname or raw_url.split('://')[-1].split(':')[0]
        except:
            host = raw_url

        # 收集面板节点
        panel_nodes = state.NODES_DATA.get(srv['url'], []) or []
        for n in panel_nodes:
            key = f"{srv['url']}|{n['id']}"
            node_lookup[key] = (n, host)

        # 收集自定义节点
        custom_nodes = srv.get('custom_nodes', []) or []
        for n in custom_nodes:
            key = f"{srv['url']}|{n['id']}"
            node_lookup[key] = (n, host)

    # 2. 按照订阅中保存的顺序生成链接
    ordered_ids = sub.get('nodes', [])

    for key in ordered_ids:
        if key in node_lookup:
            node, host = node_lookup[key]

            # A. 优先使用原始链接
            if node.get('_raw_link'):
                links.append(node['_raw_link'])
            # B. 生成标准链接
            else:
                l = utils.generate_node_link(node, host)
                if l: links.append(l)

    return Response(utils.safe_base64("\n".join(links)), media_type="text/plain; charset=utf-8")


# ================= 分组订阅接口：支持 Tag 和 主分组 =================
async def group_sub_handler(group_b64: str, request: Request):
    group_name = utils.decode_base64_safe(group_b64)
    if not group_name: return Response("Invalid Group Name", 400)

    links = []

    # 筛选符合分组的服务器
    target_servers = [
        s for s in state.SERVERS_CACHE
        if s.get('group', '默认分组') == group_name or group_name in s.get('tags', [])
    ]

    logger.info(f"正在生成分组订阅: [{group_name}]，匹配到 {len(target_servers)} 个服务器")

    for srv in target_servers:
        # 1. 获取面板节点
        panel_nodes = state.NODES_DATA.get(srv['url'], []) or []
        # 2. 获取自定义节点
        custom_nodes = srv.get('custom_nodes', []) or []
        # === 合并 ===
        all_nodes = panel_nodes + custom_nodes

        if not all_nodes: continue

        raw_url = srv['url']
        try:
            if '://' not in raw_url: raw_url = f'http://{raw_url}'
            parsed = urlparse(raw_url);
            host = parsed.hostname or raw_url.split('://')[-1].split(':')[0]
        except:
            host = raw_url

        for n in all_nodes:
            if n.get('enable'):
                # A. 优先使用原始链接
                if n.get('_raw_link'):
                    links.append(n['_raw_link'])
                # B. 生成面板节点链接
                else:
                    l = utils.generate_node_link(n, host)
                    if l: links.append(l)

    if not links:
        return Response(f"// Group [{group_name}] is empty or not found", media_type="text/plain; charset=utf-8")

    return Response(utils.safe_base64("\n".join(links)), media_type="text/plain; charset=utf-8")


# ================= 短链接接口：分组 (完美混合版) =================
async def short_group_handler(target: str, group_b64: str, request: Request):
    try:
        group_name = utils.decode_base64_safe(group_b64)
        if not group_name: return Response("Invalid Group Name", 400)

        # -------------------------------------------------------------
        # 策略 A: 针对 Surge / Loon -> 使用 Python 原生生成 (解决 Hy2 无法转换 + VMess 格式问题)
        # -------------------------------------------------------------
        if target == 'surge':
            links = []

            # 1. 筛选服务器
            target_servers = [
                s for s in state.SERVERS_CACHE
                if s.get('group', '默认分组') == group_name or group_name in s.get('tags', [])
            ]

            # 2. 遍历服务器生成配置
            for srv in target_servers:
                panel_nodes = state.NODES_DATA.get(srv['url'], []) or []
                custom_nodes = srv.get('custom_nodes', []) or []

                # 获取干净的 Host
                raw_url = srv['url']
                try:
                    if '://' not in raw_url: raw_url = f'http://{raw_url}'
                    parsed = urlparse(raw_url)
                    host = parsed.hostname or raw_url.split('://')[-1].split(':')[0]
                except:
                    host = raw_url

                # 合并处理面板节点和自定义节点
                for n in (panel_nodes + custom_nodes):
                    if n.get('enable'):
                        # 调用我们修复后的 generate_detail_config
                        line = utils.generate_detail_config(n, host)
                        if line and not line.startswith('//') and not line.startswith('None'):
                            links.append(line)

            if not links:
                return Response(f"// Group [{group_name}] is empty", media_type="text/plain; charset=utf-8")

            return Response("\n".join(links), media_type="text/plain; charset=utf-8")

        # -------------------------------------------------------------
        # 策略 B: 针对 Clash / 其他 -> 继续使用 SubConverter
        # -------------------------------------------------------------
        custom_base = state.ADMIN_CONFIG.get('manager_base_url', '').strip().rstrip('/')
        if custom_base:
            base_url = custom_base
        else:
            host = request.headers.get('host')
            scheme = request.url.scheme
            base_url = f"{scheme}://{host}"

        internal_api = f"{base_url}/sub/group/{group_b64}"

        # 关键参数：scv=true (跳过证书验证), udp=true
        params = {
            "target": target,
            "url": internal_api,
            "insert": "false",
            "list": "true",
            "ver": "4",
            "udp": "true",
            "scv": "true"
        }

        converter_api = "http://subconverter:25500/sub"

        def _fetch_sync():
            try:
                return requests.get(converter_api, params=params, timeout=10)
            except:
                return None

        response = await logic.run_in_bg_executor(_fetch_sync)
        if response and response.status_code == 200:
            return Response(content=response.content, media_type="text/plain; charset=utf-8")
        else:
            return Response(f"SubConverter Error (Code: {getattr(response, 'status_code', 'Unk')})", status_code=502)

    except Exception as e:
        return Response(f"Error: {str(e)}", status_code=500)


# ================= 短链接接口：严格遵循自定义顺序 =================
async def short_sub_handler(target: str, token: str, request: Request):
    try:
        sub_obj = next((s for s in state.SUBS_CACHE if s['token'] == token), None)
        if not sub_obj: return Response("Subscription Not Found", 404)

        # -------------------------------------------------------------
        # 策略 A: 针对 Surge -> Python 原生生成 (严格顺序版)
        # -------------------------------------------------------------
        if target == 'surge':
            links = []

            # 1. 构建查找字典
            node_lookup = {}
            for srv in state.SERVERS_CACHE:
                # 解析 Host
                raw_url = srv['url']
                try:
                    if '://' not in raw_url: raw_url = f'http://{raw_url}'
                    parsed = urlparse(raw_url)
                    host = parsed.hostname or raw_url.split('://')[-1].split(':')[0]
                except:
                    host = raw_url

                # 收集所有节点
                all_nodes = (state.NODES_DATA.get(srv['url'], []) or []) + srv.get('custom_nodes', [])
                for n in all_nodes:
                    key = f"{srv['url']}|{n['id']}"
                    node_lookup[key] = (n, host)

            # 2. 按顺序生成配置
            ordered_ids = sub_obj.get('nodes', [])

            for key in ordered_ids:
                if key in node_lookup:
                    node, host = node_lookup[key]
                    # 生成 Surge 配置行
                    line = utils.generate_detail_config(node, host)
                    if line and not line.startswith('//') and not line.startswith('None'):
                        links.append(line)

            return Response("\n".join(links), media_type="text/plain; charset=utf-8")

        # -------------------------------------------------------------
        # 策略 B: Clash / 其他 -> SubConverter
        # -------------------------------------------------------------

        custom_base = state.ADMIN_CONFIG.get('manager_base_url', '').strip().rstrip('/')
        if custom_base:
            base_url = custom_base
        else:
            host = request.headers.get('host')
            scheme = request.url.scheme
            base_url = f"{scheme}://{host}"

        internal_api = f"{base_url}/sub/{token}"
        opt = sub_obj.get('options', {})

        params = {
            "target": target, "url": internal_api,
            "insert": "false", "list": "true", "ver": "4",
            "emoji": str(opt.get('emoji', True)).lower(),
            "udp": str(opt.get('udp', True)).lower(),
            "tfo": str(opt.get('tfo', False)).lower(),
            "scv": str(opt.get('skip_cert', True)).lower(),
            "fdn": "false",  # 强制不过滤域名
            "sort": "false",  # ✨✨✨ 关键：告诉 SubConverter 不要再次排序，保持原样
        }

        # 处理正则过滤 (保持原样)
        regions = opt.get('regions', [])
        includes = []
        if opt.get('include_regex'): includes.append(opt['include_regex'])
        if regions:
            region_keywords = []
            for r in regions:
                parts = r.split(' ');
                k = parts[1] if len(parts) > 1 else r
                region_keywords.append(k)
                for c, v in config.AUTO_COUNTRY_MAP.items():
                    if v == r and len(c) == 2: region_keywords.append(c)
            if region_keywords: includes.append(f"({'|'.join(region_keywords)})")

        if includes: params['include'] = "|".join(includes)
        if opt.get('exclude_regex'): params['exclude'] = opt['exclude_regex']

        ren_pat = opt.get('rename_pattern', '')
        if ren_pat: params['rename'] = f"{ren_pat}@{opt.get('rename_replacement', '')}"

        converter_api = "http://subconverter:25500/sub"

        def _fetch_sync():
            try:
                return requests.get(converter_api, params=params, timeout=10)
            except:
                return None

        response = await logic.run_in_bg_executor(_fetch_sync)
        if response and response.status_code == 200:
            return Response(content=response.content, media_type="text/plain; charset=utf-8")
        else:
            return Response(f"SubConverter Error (Code: {getattr(response, 'status_code', 'Unk')})", status_code=502)

    except Exception as e:
        return Response(f"Error: {str(e)}", status_code=500)


# ================= 探针主动注册接口=================
async def probe_register(request: Request):
    try:
        data = await request.json()

        # 1. 安全校验
        submitted_token = data.get('token')
        correct_token = state.ADMIN_CONFIG.get('probe_token')

        if not submitted_token or submitted_token != correct_token:
            return Response(json.dumps({"success": False, "msg": "Token 错误"}), status_code=403)

        # 2. 获取客户端真实 IP
        client_ip = request.headers.get("X-Forwarded-For", request.client.host).split(',')[0].strip()

        # 3. ✨✨✨ 智能查重逻辑 (核心修改) ✨✨✨
        target_server = None

        # 策略 A: 直接字符串匹配 (命中纯 IP 注册的情况)
        for s in state.SERVERS_CACHE:
            if client_ip in s['url']:
                target_server = s
                break

        # 策略 B: 如果没找到，尝试 DNS 反向解析 (命中域名注册的情况)
        if not target_server:
            logger.info(f"🔍 [探针注册] IP {client_ip} 未直接匹配，尝试解析现有域名...")
            for s in state.SERVERS_CACHE:
                try:
                    # 提取缓存中的 Host (可能是域名)
                    cached_host = s['url'].split('://')[-1].split(':')[0]

                    # 跳过已经是 IP 的
                    if re.match(r"^\d+\.\d+\.\d+\.\d+$", cached_host): continue

                    # 解析域名为 IP (使用 run.io_bound 防止阻塞)
                    resolved_ip = await logic.run_in_bg_executor(socket.gethostbyname, cached_host)

                    if resolved_ip == client_ip:
                        target_server = s
                        logger.info(f"✅ [探针注册] 域名 {cached_host} 解析为 {client_ip}，匹配成功！")
                        break
                except:
                    pass

        # 4. 逻辑分支
        if target_server:
            # === 情况 1: 已存在，仅激活探针 ===
            if not target_server.get('probe_installed'):
                target_server['probe_installed'] = True
                await logic.save_servers()  # 保存状态
                if state.refresh_dashboard_ui_func: await state.refresh_dashboard_ui_func()

            return Response(json.dumps({"success": True, "msg": "已合并现有服务器"}), status_code=200)

        else:
            # === 情况 2: 完全陌生的机器，新建 ===
            # (之前的创建逻辑保持不变)
            new_server = {
                'name': f"🏳️ {client_ip}",
                'group': '自动注册',
                'url': f"http://{client_ip}:54321",
                'user': 'admin',
                'pass': 'admin',
                'ssh_auth_type': '全局密钥',
                'probe_installed': True,
                '_status': 'online'
            }
            state.SERVERS_CACHE.append(new_server)
            await logic.save_servers()

            # 触发强制重命名
            asyncio.create_task(logic.force_geoip_naming_task(new_server))

            if state.refresh_dashboard_ui_func: await state.refresh_dashboard_ui_func()
            if state.render_sidebar_content_func: state.render_sidebar_content_func.refresh()

            logger.info(f"✨ [主动注册] 新服务器上线: {client_ip}")
            return Response(json.dumps({"success": True, "msg": "注册成功"}), status_code=200)

    except Exception as e:
        logger.error(f"❌ 注册接口异常: {e}")
        return Response(json.dumps({"success": False, "msg": str(e)}), status_code=500)


# ================= 自动注册接口 =================
async def auto_register_node(request: Request):
    try:
        # 1. 获取并解析数据
        data = await request.json()

        # 2. 安全验证
        secret = data.get('secret')
        if secret != config.AUTO_REGISTER_SECRET:
            logger.warning(f"⚠️ [自动注册] 密钥错误: {secret}")
            return Response(json.dumps({"success": False, "msg": "密钥错误"}), status_code=403,
                            media_type="application/json")

        # 3. 提取字段
        ip = data.get('ip')
        port = data.get('port')
        username = data.get('username')
        password = data.get('password')
        alias = data.get('alias', f'Auto-{ip}')

        # 可选参数
        ssh_port = data.get('ssh_port', 22)

        if not all([ip, port, username, password]):
            return Response(json.dumps({"success": False, "msg": "参数不完整"}), status_code=400,
                            media_type="application/json")

        target_url = f"http://{ip}:{port}"

        # 4. 构建配置字典
        new_server_config = {
            'name': alias,
            'group': '默认分组',
            'url': target_url,
            'user': username,
            'pass': password,
            'prefix': '',

            # SSH 配置
            'ssh_port': ssh_port,
            'ssh_auth_type': '全局密钥',
            'ssh_user': 'detecting...',  # 初始占位符，稍后会被后台任务覆盖
            'probe_installed': False
        }

        # 5. 查重与更新逻辑
        existing_index = -1
        # 标准化 URL 进行比对
        for idx, srv in enumerate(state.SERVERS_CACHE):
            cache_url = srv['url'].replace('http://', '').replace('https://', '')
            new_url_clean = target_url.replace('http://', '').replace('https://', '')
            if cache_url == new_url_clean:
                existing_index = idx
                break

        action_msg = ""
        target_server_ref = None

        if existing_index != -1:
            # 更新现有节点
            state.SERVERS_CACHE[existing_index].update(new_server_config)
            target_server_ref = state.SERVERS_CACHE[existing_index]
            action_msg = f"🔄 更新节点: {alias}"
        else:
            # 新增节点
            state.SERVERS_CACHE.append(new_server_config)
            target_server_ref = new_server_config
            action_msg = f"✅ 新增节点: {alias}"

        # 6. 保存到硬盘
        await logic.save_servers()

        # ================= ✨✨✨ 后台任务启动区 ✨✨✨ =================

        # 任务A: 启动 GeoIP 命名任务 (自动变国旗)
        asyncio.create_task(logic.force_geoip_naming_task(target_server_ref))

        # 任务B: 启动智能 SSH 用户探测任务 (先试ubuntu，再试root，成功后装探针)
        asyncio.create_task(logic.smart_detect_ssh_user_task(target_server_ref))

        # =============================================================

        if state.render_sidebar_content_func: state.render_sidebar_content_func.refresh()

        logger.info(f"[自动注册] {action_msg} ({ip}) - 已加入 SSH 探测与命名队列")
        return Response(json.dumps({"success": True, "msg": "注册成功，后台正在探测连接..."}), status_code=200,
                        media_type="application/json")

    except Exception as e:
        logger.error(f"❌ [自动注册] 处理异常: {e}")
        return Response(json.dumps({"success": False, "msg": str(e)}), status_code=500, media_type="application/json")


# ================= 核心：前端轮询用的纯数据接口 (API) =================
async def get_dashboard_live_data():
    data = logic.calculate_dashboard_data()
    return data if data else {"error": "Calculation failed"}