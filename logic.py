# logic.py
import json
import os
import asyncio
import time
import logging
import datetime
import zipfile
import io
import shutil
import socket
import re
import requests
from concurrent.futures import ThreadPoolExecutor

import config
import state
import utils

logger = logging.getLogger("XUI_Manager")

# ================= 0. 顶层同步函数 (用于多进程调用) =================
# 必须定义在最外层，否则 ProcessPoolExecutor 无法 Pickle (报错)

def _save_json_sync(file_path, data):
    """同步写入 JSON 文件"""
    # 确保目录存在
    parent = os.path.dirname(file_path)
    if not os.path.exists(parent):
        os.makedirs(parent)

    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return True


def _save_nodes_sync(file_path, data):
    """同步写入节点缓存 (紧凑格式)"""
    parent = os.path.dirname(file_path)
    if not os.path.exists(parent):
        os.makedirs(parent)

    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)
    return True


def _zip_backup_sync(data_dir, zip_filename):
    """同步创建压缩包"""
    with zipfile.ZipFile(zip_filename, 'w') as zf:
        if os.path.exists(data_dir):
            for root, _, files in os.walk(data_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    # 保持相对路径
                    arcname = os.path.relpath(file_path, data_dir)
                    zf.write(file_path, arcname)
    return zip_filename


def _unzip_backup_sync(content_bytes, data_dir):
    """同步解压恢复"""
    try:
        with zipfile.ZipFile(io.BytesIO(content_bytes)) as zf:
            # 清空旧数据 (可选，这里选择覆盖)
            if os.path.exists(data_dir):
                shutil.rmtree(data_dir)
            os.makedirs(data_dir)
            zf.extractall(data_dir)
        return True
    except:
        return False


# ================= 1. 数据初始化与保存 =================

def init_data():
    """初始化数据目录和加载缓存"""
    # 1. 确保数据目录存在
    if not os.path.exists(config.DATA_DIR):
        os.makedirs(config.DATA_DIR)
        logger.info(f"创建数据目录: {config.DATA_DIR}")

    # 2. 加载服务器列表
    if os.path.exists(config.CONFIG_FILE):
        try:
            with open(config.CONFIG_FILE, 'r', encoding='utf-8') as f:
                state.SERVERS_CACHE = json.load(f)
            logger.info(f"✅ 成功加载服务器: {len(state.SERVERS_CACHE)} 台")
        except Exception as e:
            logger.error(f"❌ 读取 servers.json 失败: {e}")
            state.SERVERS_CACHE = []
    else:
        logger.warning(f"⚠️ 未找到服务器配置文件: {config.CONFIG_FILE}")

    # 3. 加载节点缓存
    if os.path.exists(config.NODES_CACHE_FILE):
        if os.path.isdir(config.NODES_CACHE_FILE):
            shutil.rmtree(config.NODES_CACHE_FILE)
            state.NODES_DATA = {}
        else:
            try:
                with open(config.NODES_CACHE_FILE, 'r', encoding='utf-8') as f:
                    state.NODES_DATA = json.load(f)
                total_nodes = sum(len(nodes) for nodes in state.NODES_DATA.values())
                logger.info(f"✅ 加载缓存节点: {total_nodes} 个")
            except Exception as e:
                logger.error(f"加载节点缓存失败: {e}")
                state.NODES_DATA = {}

    # 4. 加载订阅
    if os.path.exists(config.SUBS_FILE):
        try:
            with open(config.SUBS_FILE, 'r', encoding='utf-8') as f:
                state.SUBS_CACHE = json.load(f)
            logger.info(f"✅ 加载订阅: {len(state.SUBS_CACHE)} 个")
        except:
            state.SUBS_CACHE = []

    # 5. 加载管理员配置
    if os.path.exists(config.ADMIN_CONFIG_FILE):
        try:
            with open(config.ADMIN_CONFIG_FILE, 'r', encoding='utf-8') as f:
                saved_conf = json.load(f)
                state.ADMIN_CONFIG.update(saved_conf)
        except:
            pass

    # 初始化默认配置
    if 'probe_enabled' not in state.ADMIN_CONFIG:
        state.ADMIN_CONFIG['probe_enabled'] = True
    if 'probe_token' not in state.ADMIN_CONFIG:
        import uuid
        state.ADMIN_CONFIG['probe_token'] = uuid.uuid4().hex


async def save_servers():
    global GLOBAL_UI_VERSION
    try:
        await run_in_bg_executor(_save_json_sync, config.CONFIG_FILE, state.SERVERS_CACHE)
        state.GLOBAL_UI_VERSION = time.time()
        # 触发 UI 刷新钩子
        if state.refresh_dashboard_ui_func:
            await state.refresh_dashboard_ui_func()
    except Exception as e:
        logger.error(f"❌ 保存服务器失败: {e}")


async def save_subs():
    try:
        await run_in_bg_executor(_save_json_sync, config.SUBS_FILE, state.SUBS_CACHE)
    except Exception as e:
        logger.error(f"❌ 保存订阅失败: {e}")


async def save_nodes_cache():
    try:
        await run_in_bg_executor(_save_nodes_sync, config.NODES_CACHE_FILE, state.NODES_DATA)
        if state.refresh_dashboard_ui_func:
            await state.refresh_dashboard_ui_func()
    except Exception as e:
        logger.error(f"❌ 保存节点缓存失败: {e}")


async def save_admin_config():
    global GLOBAL_UI_VERSION
    try:
        await run_in_bg_executor(_save_json_sync, config.ADMIN_CONFIG_FILE, state.ADMIN_CONFIG)
        state.GLOBAL_UI_VERSION = time.time()
    except Exception as e:
        logger.error(f"❌ 配置保存失败: {e}")


# ================= 2. 核心业务逻辑 (Dashboard & Maps) =================

def calculate_dashboard_data():
    """计算仪表盘统计数据 (完整还原原版逻辑)"""
    try:
        total_servers = len(state.SERVERS_CACHE)
        online_servers = 0
        total_nodes = 0
        total_traffic_bytes = 0
        server_traffic_map = {}
        
        from collections import Counter
        country_counter = Counter()
        now_ts = time.time()

        for s in state.SERVERS_CACHE:
            url = s.get('url')
            # 获取各类数据
            res = state.NODES_DATA.get(url, []) or []
            custom = s.get('custom_nodes', []) or []
            probe_data = state.PROBE_DATA_CACHE.get(url)
            
            name = s.get('name', '未命名')

            # 统计区域
            try:
                region_str = detect_country_group(name, s)
                if not region_str or region_str.strip() == "🏳️":
                    region_str = "🏳️ 未知区域"
            except:
                region_str = "🏳️ 未知区域"
            country_counter[region_str] += 1

            # 计算流量 (优先探针)
            srv_traffic = 0
            use_probe_traffic = False

            if s.get('probe_installed') and probe_data:
                t_in = probe_data.get('net_total_in', 0)
                t_out = probe_data.get('net_total_out', 0)
                if t_in > 0 or t_out > 0:
                    srv_traffic = t_in + t_out
                    use_probe_traffic = True
            
            # 兜底：累加 X-UI 节点流量
            if not use_probe_traffic and res:
                for n in res:
                    srv_traffic += int(n.get('up', 0)) + int(n.get('down', 0))

            total_traffic_bytes += srv_traffic
            server_traffic_map[name] = srv_traffic

            # 判断在线状态 (优先探针心跳)
            is_online = False
            if s.get('probe_installed') and probe_data:
                if now_ts - probe_data.get('last_updated', 0) < 60:
                    is_online = True
            
            # X-UI 判定
            if not is_online:
                if res or s.get('_status') == 'online':
                    is_online = True
            
            if is_online:
                online_servers += 1

            # 统计节点数
            if res: total_nodes += len(res)
            if custom: total_nodes += len(custom)

        # 构建图表数据
        sorted_traffic = sorted(server_traffic_map.items(), key=lambda x: x[1], reverse=True)[:15]
        bar_names = [x[0] for x in sorted_traffic]
        bar_values = [round(x[1]/(1024**3), 2) for x in sorted_traffic]

        chart_data = []
        sorted_regions = country_counter.most_common()
        if len(sorted_regions) > 5:
            top_5 = sorted_regions[:5]
            others_count = sum(item[1] for item in sorted_regions[5:])
            for k, v in top_5: chart_data.append({'name': f"{k} ({v})", 'value': v})
            if others_count > 0: chart_data.append({'name': f"🏳️ 其他 ({others_count})", 'value': others_count})
        else:
            for k, v in sorted_regions: chart_data.append({'name': f"{k} ({v})", 'value': v})

        if not chart_data: chart_data = [{'name': '暂无数据', 'value': 0}]

        return {
            "servers": f"{online_servers}/{total_servers}",
            "nodes": str(total_nodes),
            "traffic": f"{total_traffic_bytes/(1024**3):.2f} GB",
            "subs": str(len(state.SUBS_CACHE)),
            "bar_chart": {"names": bar_names, "values": bar_values},
            "pie_chart": chart_data
        }
    except Exception as e:
        logger.error(f"仪表盘数据计算错误: {e}")
        return None


def detect_country_group(name, server_obj=None):
    """智能分组核心"""
    # 1. 优先手动分组
    if server_obj:
        saved_group = server_obj.get('group')
        if saved_group and saved_group not in ['默认分组', '自动注册', '未分组', '自动导入', '🏳️ 其他地区', '其他地区']:
            # 尝试标准化
            for v in config.AUTO_COUNTRY_MAP.values():
                if saved_group in v or v in saved_group:
                    return v
            return saved_group

    # 2. 关键字匹配
    name_upper = name.upper()
    sorted_keys = sorted(config.AUTO_COUNTRY_MAP.keys(), key=len, reverse=True)
    
    for key in sorted_keys:
        val = config.AUTO_COUNTRY_MAP[key]
        if key in name_upper:
            # 针对短字母缩写(如 US, SG)做边界检查
            if len(key) <= 3 and key.isalpha():
                pattern = r'(?<![A-Z0-9])' + re.escape(key) + r'(?![A-Z0-9])'
                if re.search(pattern, name_upper):
                    return val
            else:
                return val

    # 3. IP 检测字段兜底
    if server_obj and server_obj.get('_detected_region'):
        detected = server_obj['_detected_region'].upper()
        for key, val in config.AUTO_COUNTRY_MAP.items():
            if key.upper() == detected or key.upper() in detected:
                return val
            
    return '🏳️ 其他地区'


def prepare_map_data():
    """准备地图和区域统计数据"""
    try:
        city_points_map = {}
        flag_points_map = {}
        active_regions_for_highlight = set()
        region_stats = {}
        country_centroids = config.COUNTRY_CENTROIDS.copy()
        
        snapshot = list(state.SERVERS_CACHE)
        now_ts = time.time()
        temp_stats_storage = {}

        for s in snapshot:
            s_name = s.get('name', '')
            
            # --- A. 确定国旗与标准名 ---
            flag_icon = "📍"
            map_name_standard = None
            
            # 简单的匹配逻辑，实际项目中可以复用原版更复杂的 FLAG_TO_MAP_NAME
            # 这里简化演示，复用 detect_country_group
            try:
                group_str = detect_country_group(s_name, s)
                if group_str and " " in group_str:
                    flag_icon = group_str.split(' ')[0]
                    # 尝试从 MATCH_MAP 反推地图名
                    for k, v in config.MATCH_MAP.items():
                        if k == flag_icon:
                            map_name_standard = v
                            break
            except: pass

            # --- B. 确定坐标 ---
            lat, lon = None, None
            if 'lat' in s and 'lon' in s:
                lat, lon = s['lat'], s['lon']
            else:
                c = utils.get_coords_from_name(s_name)
                if c: lat, lon = c[0], c[1]
            
            # --- C. 生成数据点 ---
            if lat and lon:
                city_points_map[f"{lat},{lon}"] = {'name': s_name, 'value': [lon, lat]}
                
                # --- D. 聚合统计 ---
                if not map_name_standard: map_name_standard = "Unknown"
                
                if map_name_standard not in temp_stats_storage:
                    cn_name = map_name_standard
                    try:
                        if group_str and ' ' in group_str: cn_name = group_str.split(' ')[1]
                    except: pass
                    
                    temp_stats_storage[map_name_standard] = {
                        'flag': flag_icon, 'cn': cn_name, 'total': 0, 'online': 0, 'servers': []
                    }
                
                rs = temp_stats_storage[map_name_standard]
                rs['total'] += 1
                
                # 在线判断
                is_on = False
                probe = state.PROBE_DATA_CACHE.get(s['url'])
                if probe and (now_ts - probe.get('last_updated', 0) < 20): is_on = True
                elif s.get('_status') == 'online': is_on = True
                
                if is_on: rs['online'] += 1
                rs['servers'].append({'name': s_name, 'status': 'online' if is_on else 'offline'})
                
                active_regions_for_highlight.add(map_name_standard)

        return (
            json.dumps({'cities': list(city_points_map.values()), 'flags': [], 'regions': list(active_regions_for_highlight)}, ensure_ascii=False),
            [], # pie data 已在 calculate_dashboard 中处理
            len(temp_stats_storage),
            json.dumps(temp_stats_storage, ensure_ascii=False),
            json.dumps(country_centroids, ensure_ascii=False)
        )
    except Exception as e:
        logger.error(f"Map data error: {e}")
        return ("{}", [], 0, "{}", "{}")


async def generate_smart_name(server_conf):
    """尝试获取面板节点名，获取不到则用 GeoIP+序号"""
    # 1. 尝试连接面板获取节点名
    try:
        mgr = get_manager(server_conf)
        if mgr:
            inbounds = await run_in_bg_executor(mgr.get_inbounds)
            if inbounds and len(inbounds) > 0:
                for node in inbounds:
                    if node.get('remark'):
                        # 自动补全国旗
                        raw_name = node['remark']
                        url = server_conf['url']
                        host = server_conf.get('ssh_host') or url.split('://')[-1].split(':')[0]
                        # 查 IP 
                        flag = await run_in_bg_executor(utils.get_flag_from_ip, host)
                        if flag and flag not in raw_name:
                            return f"{flag} {raw_name}"
                        return raw_name
    except: pass

    # 2. 尝试 GeoIP 命名
    try:
        url = server_conf.get('url', '')
        host = server_conf.get('ssh_host')
        if not host and url: host = url.replace('http://', '').replace('https://', '').split(':')[0]
        
        if host:
            flag = await run_in_bg_executor(utils.get_flag_from_ip, host)
            # 查找国家名
            country = "Server"
            for f, c in config.AUTO_COUNTRY_MAP.items():
                if f == flag:
                    country = c.split(' ')[1] if ' ' in c else c
                    break
            
            # 计算序号
            count = 1
            for s in state.SERVERS_CACHE:
                if s.get('name', '').startswith(f"{flag} {country}"): count += 1
            
            return f"{flag} {country}-{count}"
    except: pass

    return f"Server-{len(state.SERVERS_CACHE) + 1}"


# ================= 3. 任务调度与后台执行 =================

async def run_in_bg_executor(func, *args):
    """通用后台线程池调用"""
    loop = asyncio.get_running_loop()
    if state.PROCESS_POOL is None:
        # 如果进程池未初始化，回退到默认线程池
        return await loop.run_in_executor(None, func, *args)
    return await loop.run_in_executor(state.PROCESS_POOL, func, *args)


async def get_server_status(server_conf):
    """获取单台服务器状态 (优先探针，其次 API) - 完整版"""
    url = server_conf.get('url')
    
    # 1. 优先读取探针缓存
    if server_conf.get('probe_installed') or url in state.PROBE_DATA_CACHE:
        cache = state.PROBE_DATA_CACHE.get(url)
        if cache:
            # 检查数据新鲜度 (20秒超时)
            if time.time() - cache.get('last_updated', 0) < 20:
                return cache
            else:
                return {'status': 'offline', 'msg': '探针超时'}

    # 2. API 模式兜底
    if server_conf.get('user'):
        # 只要之前的轮询标记为 online，就返回一个假的在线状态供 UI 显示
        if server_conf.get('_status') == 'online':
             return {
                 'status': 'online', 
                 'msg': 'API Online', 
                 'cpu_usage': 0, 
                 'mem_usage': 0,
                 'uptime': 'API 托管中'
             }
    
    return {'status': 'offline', 'msg': '未连接'}


async def send_telegram_message(text):
    """发送 TG 消息"""
    token = state.ADMIN_CONFIG.get('tg_bot_token')
    chat_id = state.ADMIN_CONFIG.get('tg_chat_id')
    if not token or not chat_id: return
    
    def _post():
        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}, timeout=5)
        except: pass

    await run_in_bg_executor(_post)


async def job_monitor_status():
    """定时任务：服务器状态监控与报警 (完整版)"""
    # 确保状态字典存在
    if not hasattr(state, 'FAILURE_COUNTS'): state.FAILURE_COUNTS = {}
    if not hasattr(state, 'ALERT_CACHE'): state.ALERT_CACHE = {}
    
    # 50 并发
    sema = asyncio.Semaphore(50)
    FAILURE_THRESHOLD = 3
    current_time = time.strftime("%H:%M:%S", time.localtime())
    
    async def _check(srv):
        # 仅监控已安装探针的机器
        if not srv.get('probe_installed', False): return
        
        async with sema:
            url = srv['url']
            name = srv.get('name', 'Unk')
            
            # 获取状态
            st = await get_server_status(srv)
            is_online = (st.get('status') == 'online')
            
            # 只有配置了 TG 才报警
            if not state.ADMIN_CONFIG.get('tg_bot_token'): return

            display_ip = url.split('://')[-1].split(':')[0]

            if is_online:
                state.FAILURE_COUNTS[url] = 0
                if state.ALERT_CACHE.get(url) == 'offline':
                    msg = f"🟢 **恢复：服务器已上线**\n🖥️ `{name}`\n🔗 `{display_ip}`\n🕒 `{current_time}`"
                    await send_telegram_message(msg)
                    state.ALERT_CACHE[url] = 'online'
            else:
                cnt = state.FAILURE_COUNTS.get(url, 0) + 1
                state.FAILURE_COUNTS[url] = cnt
                
                if cnt >= FAILURE_THRESHOLD:
                    if state.ALERT_CACHE.get(url) != 'offline':
                        msg = f"🔴 **警告：服务器离线**\n🖥️ `{name}`\n🔗 `{display_ip}`\n🕒 `{current_time}`"
                        await send_telegram_message(msg)
                        state.ALERT_CACHE[url] = 'offline'

    tasks = [_check(s) for s in state.SERVERS_CACHE]
    if tasks: await asyncio.gather(*tasks)


async def job_sync_all_traffic():
    """定时任务：同步所有 API 节点流量"""
    logger.info("🕒 [智能同步] 检查 API 节点同步...")
    tasks = []
    for s in state.SERVERS_CACHE:
        # 跳过探针机器，只同步纯 API 机器
        if s.get('url') and not s.get('probe_installed'):
            tasks.append(fetch_inbounds_safe(s, force_refresh=True))
    
    if tasks:
        await asyncio.gather(*tasks)
        await save_nodes_cache()
        # 触发 UI 刷新
        if state.refresh_dashboard_ui_func: await state.refresh_dashboard_ui_func()


async def job_check_geo_ip():
    """后台任务：解析 IP 归属地并更新国旗"""
    logger.info("🌍 [定时任务] IP 归属地检测...")
    changed = False
    
    # 动态生成已知国旗列表
    known_flags = []
    for val in config.AUTO_COUNTRY_MAP.values():
        icon = val.split(' ')[0]
        if icon: known_flags.append(icon)

    for s in state.SERVERS_CACHE:
        old_name = s.get('name', '')
        new_name = old_name

        # 1. 清洗白旗
        if new_name.startswith('🏳️'):
            if len(new_name) > 2:
                new_name = new_name.replace('🏳️', '').strip()

        # 2. 如果没国旗，去获取
        has_flag = any(f in new_name for f in known_flags)
        if not has_flag:
            try:
                host = s.get('ssh_host') or s.get('url', '').split('://')[-1].split(':')[0]
                if not host: continue
                # 解析域名
                if not re.match(r"^\d+\.\d+\.\d+\.\d+$", host):
                    host = await run_in_bg_executor(socket.gethostbyname, host)
                
                flag = await run_in_bg_executor(utils.get_flag_from_ip, host)
                if flag and flag != "🏳️":
                    new_name = f"{flag} {new_name}"
            except: pass
        
        if new_name != old_name:
            s['name'] = new_name
            # 自动分组
            s['group'] = detect_country_group(new_name, s)
            changed = True
            
    if changed:
        await save_servers()
        if state.render_sidebar_content_func: state.render_sidebar_content_func.refresh()


# ================= 4. 节点获取与管理 =================

async def fetch_inbounds_safe(server_conf, force_refresh=False, sync_name=False):
    """获取节点统一入口"""
    url = server_conf.get('url')
    
    # 自动命名逻辑
    if sync_name:
        new_name = await generate_smart_name(server_conf)
        if new_name != server_conf.get('name'):
            server_conf['name'] = new_name
            server_conf['group'] = detect_country_group(new_name, server_conf)
            await save_servers()

    # 探针模式：直接读缓存 (守门员逻辑)
    if server_conf.get('probe_installed'):
        return state.NODES_DATA.get(url, [])

    # API 模式：读取缓存或请求网络
    if not url or not server_conf.get('user'): return []
    
    # 缓存命中逻辑
    if not force_refresh and url in state.NODES_DATA:
        return state.NODES_DATA[url]

    try:
        mgr = get_manager(server_conf)
        if not mgr: return []
        
        # 放入线程池执行
        if hasattr(mgr, 'get_inbounds'):
            nodes = await run_in_bg_executor(mgr.get_inbounds)
            if nodes is not None:
                state.NODES_DATA[url] = nodes
                server_conf['_status'] = 'online'
                return nodes
    except Exception as e:
        server_conf['_status'] = 'offline'
    
    return state.NODES_DATA.get(url, [])


def get_manager(server_conf):
    """工厂函数"""
    # 优先 SSH
    if server_conf.get('ssh_host') and server_conf.get('ssh_user'):
        from utils import XUI_SSH_Manager
        return XUI_SSH_Manager(server_conf)
    # 其次 API
    if server_conf.get('url') and server_conf.get('user'):
        from utils import XUI_API_Manager
        return XUI_API_Manager(server_conf)
    return None


# ================= 5. 探针/SSH 操作 =================

async def install_probe_on_server(server_conf):
    """单台安装探针 (完整脚本逻辑)"""
    # 获取本机IP作为默认回调
    my_ip = "127.0.0.1"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80)); my_ip = s.getsockname()[0]; s.close()
    except: pass

    base_url = state.ADMIN_CONFIG.get('manager_base_url', f"http://{my_ip}:8080")
    
    script = config.PROBE_INSTALL_SCRIPT \
        .replace("__MANAGER_URL__", base_url) \
        .replace("__TOKEN__", state.ADMIN_CONFIG.get('probe_token', 'default_token')) \
        .replace("__SERVER_URL__", server_conf['url']) \
        .replace("__PING_CT__", state.ADMIN_CONFIG.get('ping_target_ct', '202.102.192.68')) \
        .replace("__PING_CU__", state.ADMIN_CONFIG.get('ping_target_cu', '112.122.10.26')) \
        .replace("__PING_CM__", state.ADMIN_CONFIG.get('ping_target_cm', '211.138.180.2'))

    utils.safe_notify(f"正在安装探针: {server_conf['name']}...", "ongoing")
    success, output = await run_in_bg_executor(utils._ssh_exec_wrapper, server_conf, script)
    
    if success:
        server_conf['probe_installed'] = True
        await save_servers()
        utils.safe_notify(f"✅ {server_conf['name']} 探针安装成功", "positive")
    else:
        utils.safe_notify(f"❌ 安装失败: {output}", "negative")


async def batch_install_all_probes():
    """批量安装"""
    utils.safe_notify("开始批量更新探针...", "ongoing")
    tasks = []
    # 限制并发
    sema = asyncio.Semaphore(10)
    
    async def _worker(s):
        async with sema:
            await install_probe_on_server(s)

    for s in state.SERVERS_CACHE:
        if s.get('ssh_host'):
            tasks.append(_worker(s))
            
    if tasks: await asyncio.gather(*tasks)
    utils.safe_notify("批量任务结束", "positive")


async def force_geoip_naming_task(server_conf):
    """强制 GeoIP 命名 (自动注册时调用)"""
    await asyncio.sleep(2)
    # 复用 smart name 逻辑
    new_name = await generate_smart_name(server_conf)
    if new_name != server_conf.get('name'):
        server_conf['name'] = new_name
        server_conf['group'] = detect_country_group(new_name, server_conf)
        await save_servers()
        if state.render_sidebar_content_func: state.render_sidebar_content_func.refresh()


async def smart_detect_ssh_user_task(server_conf):
    """智能探测 SSH 用户名 (完整轮询逻辑)"""
    candidates = ['root', 'ubuntu', 'debian', 'opc', 'ec2-user', 'admin']
    ip = server_conf.get('ssh_host') or server_conf.get('url').split('://')[-1].split(':')[0]
    
    logger.info(f"🕵️‍♂️ 正在探测 SSH 用户: {ip}")
    
    found = None
    original_user = server_conf.get('ssh_user')

    for user in candidates:
        server_conf['ssh_user'] = user
        # 尝试连接
        client, msg = await run_in_bg_executor(utils.get_ssh_client_sync, server_conf)
        if client:
            client.close()
            found = user
            logger.info(f"✅ 探测成功: {user}@{ip}")
            break
            
    if found:
        server_conf['ssh_user'] = found
        await save_servers()
        # 探测成功后自动安装探针
        if state.ADMIN_CONFIG.get('probe_enabled', False):
            await asyncio.sleep(1)
            await install_probe_on_server(server_conf)
    else:
        logger.warning(f"❌ 探测失败: {ip}")
        # 恢复默认
        if original_user: server_conf['ssh_user'] = original_user
        else: server_conf['ssh_user'] = 'root'
        await save_servers()


def record_ping_history(url, pings):
    """记录 Ping 历史"""
    if url not in state.PING_TREND_CACHE: state.PING_TREND_CACHE[url] = []
    
    # 防抖：同一服务器 60s 内只记录一次
    if state.PING_TREND_CACHE[url]:
        last_time = state.PING_TREND_CACHE[url][-1]['ts']
        if time.time() - last_time < 60: return

    now = time.time()
    rec = {
        'ts': now,
        'time_str': datetime.datetime.fromtimestamp(now).strftime('%m/%d %H:%M'),
        'ct': pings.get('电信', -1),
        'cu': pings.get('联通', -1),
        'cm': pings.get('移动', -1)
    }
    state.PING_TREND_CACHE[url].append(rec)
    if len(state.PING_TREND_CACHE[url]) > 1440: # 24h
        state.PING_TREND_CACHE[url] = state.PING_TREND_CACHE[url][-1440:]


# ================= 6. 备份/恢复 (顶层) =================
async def create_backup_zip():
    if not os.path.exists('backup'): os.makedirs('backup')
    name = f"backup/backup_{int(time.time())}.zip"
    return await run_in_bg_executor(_zip_backup_sync, config.DATA_DIR, name)


async def restore_backup_zip(content):
    res = await run_in_bg_executor(_unzip_backup_sync, content, config.DATA_DIR)
    if res: init_data()
    return res


# ================= 7. 智能修正 (遗漏的补全) =================
async def fast_resolve_single_server(s):
    """
    后台全自动修正流程：
    1. 尝试连接面板，读取第一个节点的备注名 (Smart Name)
    2. 尝试查询 IP 归属地，获取国旗 (GeoIP)
    3. 自动组合名字 (防止国旗重复)
    4. 自动归类分组
    """
    await asyncio.sleep(1.5) # 稍微错峰
    
    url = s.get('url', '')
    if not url: return
    raw_ip = url.split('://')[-1].split(':')[0]
    logger.info(f"🔍 [智能修正] 正在处理: {raw_ip} ...")
    
    data_changed = False
    
    try:
        # --- 步骤 1: 尝试从面板获取真实备注 ---
        current_pure_name = s['name'].replace('🏳️', '').strip()
        
        # 只有当名字看起来像默认 IP (或带白旗的IP) 时，才去面板读取
        if current_pure_name == raw_ip or current_pure_name.startswith('Server'):
            try:
                # 强制刷新获取最新节点
                nodes = await fetch_inbounds_safe(s, force_refresh=True)
                if nodes and len(nodes) > 0:
                    smart_name = nodes[0].get('remark', '').strip()
                    if smart_name and smart_name != raw_ip:
                        s['name'] = smart_name
                        data_changed = True
                        logger.info(f"🏷️ [获取备注] 成功: {smart_name}")
            except Exception as e:
                logger.warning(f"⚠️ [获取备注] 失败: {e}")

        # --- 步骤 2: 查 IP 归属地并修正国旗/分组 ---
        host = s.get('ssh_host') or raw_ip
        try:
            if not re.match(r"^\d+\.\d+\.\d+\.\d+$", host):
                host = await run_in_bg_executor(socket.gethostbyname, host)
        except: pass

        flag = await run_in_bg_executor(utils.get_flag_from_ip, host)
        
        if flag and flag != "🏳️":
            # 重置坐标让地图重新获取
            s['lat'] = None; s['lon'] = None
            
            # 国旗防重复逻辑
            temp_name = s['name'].replace('🏳️', '').strip()
            
            if flag in temp_name:
                if s['name'] != temp_name:
                    s['name'] = temp_name
                    data_changed = True
            else:
                s['name'] = f"{flag} {temp_name}"
                data_changed = True

            # 强制自动分组
            target_group = detect_country_group(s['name'], s)
            if s.get('group') in ['默认分组', '自动注册', '未分组'] and target_group != '🏳️ 其他地区':
                s['group'] = target_group
                data_changed = True

        # --- 步骤 4: 保存变更 ---
        if data_changed:
            await save_servers()
            if state.refresh_dashboard_ui_func: await state.refresh_dashboard_ui_func()
            if state.render_sidebar_content_func: state.render_sidebar_content_func.refresh()
            logger.info(f"✅ [智能修正] 完毕: {s['name']} -> [{s['group']}]")
            
    except Exception as e:
        logger.error(f"❌ [智能修正] 严重错误: {e}")
