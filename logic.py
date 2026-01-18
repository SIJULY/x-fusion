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

def _save_json_sync(file_path, data):
    """同步写入 JSON 文件"""
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
                    arcname = os.path.relpath(file_path, data_dir)
                    zf.write(file_path, arcname)
    return zip_filename

def _unzip_backup_sync(content_bytes, data_dir):
    """同步解压恢复"""
    try:
        with zipfile.ZipFile(io.BytesIO(content_bytes)) as zf:
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
    if not os.path.exists(config.DATA_DIR):
        os.makedirs(config.DATA_DIR)
        logger.info(f"创建数据目录: {config.DATA_DIR}")

    # 1. 加载服务器列表
    if os.path.exists(config.CONFIG_FILE):
        try:
            with open(config.CONFIG_FILE, 'r', encoding='utf-8') as f:
                state.SERVERS_CACHE = json.load(f)
            logger.info(f"✅ 加载服务器: {len(state.SERVERS_CACHE)} 台")
        except Exception as e:
            logger.error(f"加载服务器配置失败: {e}")
            state.SERVERS_CACHE = []
    else:
        logger.warning(f"⚠️ 未找到服务器配置文件: {config.CONFIG_FILE}")

    # 2. 加载节点缓存
    if os.path.exists(config.NODES_CACHE_FILE):
        try:
            with open(config.NODES_CACHE_FILE, 'r', encoding='utf-8') as f:
                state.NODES_DATA = json.load(f)
            total_nodes = sum(len(nodes) for nodes in state.NODES_DATA.values())
            logger.info(f"✅ 加载缓存节点: {total_nodes} 个")
        except Exception as e:
            logger.error(f"加载节点缓存失败: {e}")

    # 3. 加载订阅
    if os.path.exists(config.SUBS_FILE):
        try:
            with open(config.SUBS_FILE, 'r', encoding='utf-8') as f:
                state.SUBS_CACHE = json.load(f)
            logger.info(f"✅ 加载订阅: {len(state.SUBS_CACHE)} 个")
        except:
            pass

    # 4. 加载管理员配置
    if os.path.exists(config.ADMIN_CONFIG_FILE):
        try:
            with open(config.ADMIN_CONFIG_FILE, 'r', encoding='utf-8') as f:
                saved_conf = json.load(f)
                state.ADMIN_CONFIG.update(saved_conf)
        except:
            pass

async def save_servers():
    try:
        await run_in_bg_executor(_save_json_sync, config.CONFIG_FILE, state.SERVERS_CACHE)
    except Exception as e:
        logger.error(f"保存服务器失败: {e}")

async def save_subs():
    try:
        await run_in_bg_executor(_save_json_sync, config.SUBS_FILE, state.SUBS_CACHE)
    except Exception as e:
        logger.error(f"保存订阅失败: {e}")

async def save_nodes_cache():
    try:
        await run_in_bg_executor(_save_nodes_sync, config.NODES_CACHE_FILE, state.NODES_DATA)
    except Exception as e:
        logger.error(f"保存节点缓存失败: {e}")

async def save_admin_config():
    try:
        await run_in_bg_executor(_save_json_sync, config.ADMIN_CONFIG_FILE, state.ADMIN_CONFIG)
    except:
        pass

# ================= 2. 核心业务逻辑 (Dashboard & Maps) =================

def calculate_dashboard_data():
    """计算仪表盘统计数据"""
    try:
        total_servers = len(state.SERVERS_CACHE)
        online_servers = len([s for s in state.SERVERS_CACHE if s.get('_status') == 'online'])
        total_nodes = 0
        total_up = 0
        total_down = 0
        traffic_rank = []

        for srv in state.SERVERS_CACHE:
            url = srv.get('url')
            api_nodes = state.NODES_DATA.get(url, []) or []
            custom_nodes = srv.get('custom_nodes', []) or []
            probe_data = state.PROBE_DATA_CACHE.get(url)

            srv_traffic = 0
            use_probe_traffic = False

            # 优先使用探针流量
            if srv.get('probe_installed') and probe_data:
                t_in = probe_data.get('net_total_in', 0)
                t_out = probe_data.get('net_total_out', 0)
                if t_in > 0 or t_out > 0:
                    srv_traffic = t_in + t_out
                    use_probe_traffic = True
            
            # 否则统计节点流量
            if not use_probe_traffic:
                for n in api_nodes:
                    srv_traffic += int(n.get('up', 0)) + int(n.get('down', 0))

            total_nodes += len(api_nodes) + len(custom_nodes)
            
            if srv_traffic > 0:
                traffic_rank.append({
                    'name': srv.get('name', 'Unknown'),
                    'value': round(srv_traffic / 1024**3, 2)
                })
            
            # 简单累加总流量用于显示
            if use_probe_traffic:
                total_up += probe_data.get('net_total_out', 0)
                total_down += probe_data.get('net_total_in', 0)
            else:
                for n in api_nodes:
                    total_up += int(n.get('up', 0))
                    total_down += int(n.get('down', 0))

        traffic_rank.sort(key=lambda x: x['value'], reverse=True)
        top_10 = traffic_rank[:10]
        
        bar_chart_data = {'names': [x['name'] for x in top_10], 'values': [x['value'] for x in top_10]}

        from collections import Counter
        region_cnt = Counter()
        for s in state.SERVERS_CACHE:
            group = detect_country_group(s.get('name', ''), s)
            region_cnt[group] += 1
        
        pie_data = []
        most_common = region_cnt.most_common(5)
        for k, v in most_common: pie_data.append({'name': f"{k} ({v})", 'value': v})
        others = sum(region_cnt.values()) - sum(x[1] for x in most_common)
        if others > 0: pie_data.append({'name': f"🏳️ 其他 ({others})", 'value': others})

        return {
            "servers": f"{online_servers} / {total_servers}",
            "nodes": str(total_nodes),
            "traffic": utils.format_bytes(total_up + total_down),
            "subs": str(len(state.SUBS_CACHE)),
            "bar_chart": bar_chart_data,
            "pie_chart": pie_data
        }
    except Exception as e:
        logger.error(f"仪表盘数据计算错误: {e}")
        return None

def detect_country_group(name, server_obj=None):
    if server_obj and server_obj.get('group') and server_obj['group'] not in ['默认分组', '自动注册', '未分组']:
        return server_obj['group']
    for flag, country in config.AUTO_COUNTRY_MAP.items():
        if flag in name: return country
    name_lower = name.lower()
    for key, country in config.AUTO_COUNTRY_MAP.items():
        if len(key) > 2 and key.lower() in name_lower: return country
    return "🏳️ 其他地区"

def prepare_map_data():
    """准备地图和区域统计数据"""
    try:
        city_points_map = {}
        flag_points_map = {}
        unique_deployed_countries = set()
        region_stats = {}
        active_regions_for_highlight = set()
        
        # 简化版映射逻辑，实际可扩展
        country_centroids = config.COUNTRY_CENTROIDS.copy()
        
        snapshot = list(state.SERVERS_CACHE)
        now_ts = time.time()
        temp_stats = {}

        for s in snapshot:
            s_name = s.get('name', '')
            # 尝试提取国旗
            flag_icon = "📍"
            try:
                g = detect_country_group(s_name, s)
                if g and " " in g: flag_icon = g.split(" ")[0]
            except: pass

            # 坐标
            lat, lon = s.get('lat'), s.get('lon')
            if not lat:
                c = utils.get_coords_from_name(s_name)
                if c: lat, lon = c[0], c[1]
            
            if lat and lon:
                city_points_map[f"{lat},{lon}"] = {'name': s_name, 'value': [lon, lat]}
                # 统计
                c_name = detect_country_group(s_name, s)
                if c_name not in temp_stats:
                     temp_stats[c_name] = {'flag': flag_icon, 'cn': c_name, 'total': 0, 'online': 0, 'servers': []}
                
                rs = temp_stats[c_name]
                rs['total'] += 1
                
                # 在线判断
                is_on = False
                probe = state.PROBE_DATA_CACHE.get(s['url'])
                if probe and (now_ts - probe.get('last_updated', 0) < 20): is_on = True
                elif s.get('_status') == 'online': is_on = True
                
                if is_on: rs['online'] += 1
                rs['servers'].append({'name': s_name, 'status': 'online' if is_on else 'offline'})
                
                # 记录高亮区域 (简单处理，假设 c_name 包含英文或能在 MAP_NAME_ALIASES 找到)
                # 这里为了简化，直接尝试匹配 config.MATCH_MAP
                for k, v in config.MATCH_MAP.items():
                    if k in flag_icon: active_regions_for_highlight.add(v)

        return (
            json.dumps({'cities': list(city_points_map.values()), 'flags': [], 'regions': list(active_regions_for_highlight)}, ensure_ascii=False),
            [], # pie data handled in calculate_dashboard
            len(temp_stats),
            json.dumps(temp_stats, ensure_ascii=False),
            json.dumps(country_centroids, ensure_ascii=False)
        )
    except Exception as e:
        logger.error(f"Map data error: {e}")
        return ("{}", [], 0, "{}", "{}")

async def generate_smart_name(server_conf):
    """自动生成名称"""
    url = server_conf.get('url', '')
    host = server_conf.get('ssh_host')
    if not host and url: host = url.replace('http://', '').replace('https://', '').split(':')[0]
    
    if not host: return "Server"
    
    flag = await run_in_bg_executor(utils.get_flag_from_ip, host)
    # 尝试反查区域名
    country = "Unknown"
    for f, c in config.AUTO_COUNTRY_MAP.items():
        if f == flag: country = c.split(' ')[1] if ' ' in c else c; break
    
    return f"{flag} {country}"

# ================= 3. 任务调度与后台执行 =================

async def run_in_bg_executor(func, *args):
    loop = asyncio.get_running_loop()
    if state.PROCESS_POOL is None:
        return await loop.run_in_executor(None, func, *args)
    return await loop.run_in_executor(state.PROCESS_POOL, func, *args)

async def get_server_status(server_conf):
    """获取单台服务器状态 (优先探针，其次 API)"""
    url = server_conf.get('url')
    
    # 1. 优先读取探针缓存
    if server_conf.get('probe_installed') or url in state.PROBE_DATA_CACHE:
        cache = state.PROBE_DATA_CACHE.get(url)
        if cache:
            if time.time() - cache.get('last_updated', 0) < 20: # 严格一点 20s
                return cache
            else:
                return {'status': 'offline', 'msg': '探针超时'}

    # 2. API 模式兜底
    if server_conf.get('user'):
        if server_conf.get('_status') == 'online':
             # 构造简单状态
             return {'status': 'online', 'msg': 'API Online', 'cpu_usage': 0, 'mem_usage': 0}
    
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
    """定时任务：服务器状态监控与报警"""
    if not hasattr(state, 'FAILURE_COUNTS'): state.FAILURE_COUNTS = {}
    if not hasattr(state, 'ALERT_CACHE'): state.ALERT_CACHE = {}
    
    sema = asyncio.Semaphore(50)
    FAILURE_THRESHOLD = 3
    current_time = time.strftime("%H:%M:%S", time.localtime())
    
    async def _check(srv):
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
        if s.get('url') and not s.get('probe_installed'):
            tasks.append(fetch_inbounds_safe(s))
    
    if tasks:
        await asyncio.gather(*tasks)
        await save_nodes_cache()
        if state.refresh_dashboard_ui_func: await state.refresh_dashboard_ui_func()

async def job_check_geo_ip():
    """后台任务：解析 IP 归属地并更新国旗"""
    logger.info("🌍 [定时任务] IP 归属地检测...")
    changed = False
    for s in state.SERVERS_CACHE:
        if "🏳️" in s.get('name', '') or not any(x in s.get('name', '') for x in ["🇨🇳","🇺🇸","🇭🇰","🇯🇵"]):
            try:
                host = s.get('ssh_host') or s.get('url', '').split('://')[-1].split(':')[0]
                if not host: continue
                # 解析
                if not re.match(r"^\d+\.\d+\.\d+\.\d+$", host):
                    host = socket.gethostbyname(host)
                
                flag = await run_in_bg_executor(utils.get_flag_from_ip, host)
                if flag and flag != "🏳️" and flag not in s['name']:
                    clean = s['name'].replace("🏳️", "").strip()
                    s['name'] = f"{flag} {clean}"
                    s['group'] = detect_country_group(s['name'])
                    changed = True
            except: pass
            
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

    # 探针模式：直接读缓存
    if server_conf.get('probe_installed'):
        return state.NODES_DATA.get(url, [])

    # API 模式
    if not url or not server_conf.get('user'): return []
    
    try:
        mgr = get_manager(server_conf)
        if not mgr: return []
        
        # 兼容同步/异步
        if hasattr(mgr, 'get_inbounds'):
             # 注意：XUI_SSH_Manager 是同步的，XUI_API_Manager 也是同步的 requests
             # 但为了不阻塞，我们统统丢进线程池
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
    """单台安装探针"""
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
        .replace("__PING_CT__", state.ADMIN_CONFIG.get('ping_target_ct', '1.1.1.1')) \
        .replace("__PING_CU__", state.ADMIN_CONFIG.get('ping_target_cu', '1.1.1.1')) \
        .replace("__PING_CM__", state.ADMIN_CONFIG.get('ping_target_cm', '1.1.1.1'))

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
    for s in state.SERVERS_CACHE:
        if s.get('ssh_host'):
            tasks.append(install_probe_on_server(s))
    if tasks: await asyncio.gather(*tasks)
    utils.safe_notify("批量任务结束", "positive")

async def force_geoip_naming_task(server_conf):
    """强制 GeoIP 命名"""
    await asyncio.sleep(2)
    await generate_smart_name(server_conf) # generate_smart_name 内部会保存

async def smart_detect_ssh_user_task(server_conf):
    """智能探测 SSH 用户名"""
    candidates = ['root', 'ubuntu', 'debian', 'opc', 'ec2-user', 'admin']
    ip = server_conf.get('ssh_host') or server_conf.get('url').split(':')[1].replace('//','')
    
    logger.info(f"🕵️‍♂️ 正在探测 SSH 用户: {ip}")
    
    found = None
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
        if state.ADMIN_CONFIG.get('probe_enabled', False):
            await install_probe_on_server(server_conf)
    else:
        logger.warning(f"❌ 探测失败: {ip}")
        # 恢复默认
        server_conf['ssh_user'] = 'root'
        await save_servers()

def record_ping_history(url, pings):
    """记录 Ping 历史"""
    if url not in state.PING_TREND_CACHE: state.PING_TREND_CACHE[url] = []
    now = time.time()
    rec = {
        'ts': now,
        'time_str': datetime.datetime.fromtimestamp(now).strftime('%H:%M'),
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

# [logic.py] 追加到文件末尾

async def fast_resolve_single_server(s):
    """
    后台全自动修正流程：
    1. 尝试连接面板，读取第一个节点的备注名 (Smart Name)
    2. 尝试查询 IP 归属地，获取国旗 (GeoIP)
    3. 自动组合名字 (防止国旗重复)
    4. 自动归类分组
    """
    await asyncio.sleep(1.5) # 稍微错峰，避免阻塞 UI 响应
    
    url = s.get('url', '')
    raw_ip = url.split('://')[-1].split(':')[0]
    logger.info(f"🔍 [智能修正] 正在处理: {raw_ip} ...")
    
    data_changed = False
    
    try:
        # --- 步骤 1: 尝试从面板获取真实备注 ---
        # 只有当名字看起来像默认 IP (或带白旗的IP) 时，才去面板读取
        current_pure_name = s['name'].replace('🏳️', '').strip()
        
        # 如果名字就是 IP，或者是以 Server 开头，尝试获取真实节点名
        if current_pure_name == raw_ip or current_pure_name.startswith('Server'):
            try:
                # 强制刷新获取最新节点
                nodes = await fetch_inbounds_safe(s, force_refresh=True)
                if nodes and len(nodes) > 0:
                    smart_name = nodes[0].get('remark', '').strip()
                    # 如果获取到了有效名字
                    if smart_name and smart_name != raw_ip:
                        s['name'] = smart_name
                        data_changed = True
                        logger.info(f"🏷️ [获取备注] 成功: {smart_name}")
            except Exception as e:
                logger.warning(f"⚠️ [获取备注] 失败: {e}")

        # --- 步骤 2: 查 IP 归属地并修正国旗/分组 ---
        # 解析真实 Host (优先 SSH Host，其次 URL)
        host = s.get('ssh_host') or raw_ip
        
        # 如果是域名，尝试解析为 IP
        import socket
        try:
            if not re.match(r"^\d+\.\d+\.\d+\.\d+$", host):
                host = await run_in_bg_executor(socket.gethostbyname, host)
        except: pass

        # 查询 GeoIP
        flag = await run_in_bg_executor(utils.get_flag_from_ip, host)
        
        if flag and flag != "🏳️":
            # 获取正确的国旗
            s['lat'] = None # 重置坐标让地图重新获取
            s['lon'] = None
            
            # ✨✨✨ [核心修复] 国旗防重复逻辑 ✨✨✨
            # 1. 先把白旗去掉，拿到干净的名字
            temp_name = s['name'].replace('🏳️', '').strip()
            
            # 2. 检查名字里是否已经包含了正确的国旗
            if flag in temp_name:
                # 如果包含了，只更新去掉白旗后的样子
                if s['name'] != temp_name:
                    s['name'] = temp_name
                    data_changed = True
            else:
                # 3. 如果没包含，加到最前面
                s['name'] = f"{flag} {temp_name}"
                data_changed = True

            # --- 步骤 3: 强制自动分组 ---
            # 尝试根据新名字自动判断分组
            target_group = detect_country_group(s['name'], s)
            
            # 只有当当前分组是默认分组时，才自动归类
            if s.get('group') in ['默认分组', '自动注册', '未分组'] and target_group != '🏳️ 其他地区':
                s['group'] = target_group
                data_changed = True
        else:
            # 没查到 IP 信息
            pass

        # --- 步骤 4: 保存变更 ---
        if data_changed:
            await save_servers()
            # 刷新 UI
            if state.refresh_dashboard_ui_func: await state.refresh_dashboard_ui_func()
            if state.render_sidebar_content_func: state.render_sidebar_content_func.refresh()
            logger.info(f"✅ [智能修正] 完毕: {s['name']} -> [{s['group']}]")
            
    except Exception as e:
        logger.error(f"❌ [智能修正] 严重错误: {e}")
