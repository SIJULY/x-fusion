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
                    arcname = os.path.relpath(file_path, data_dir)
                    zf.write(file_path, arcname)
    return zip_filename


def _unzip_backup_sync(content_bytes, data_dir):
    """同步解压恢复"""
    try:
        with zipfile.ZipFile(io.BytesIO(content_bytes)) as zf:
            # 清空 data 目录
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

    # 2. 加载节点缓存 (防止重启后流量数据丢失)
    if os.path.exists(config.NODES_CACHE_FILE):
        try:
            with open(config.NODES_CACHE_FILE, 'r', encoding='utf-8') as f:
                state.NODES_DATA = json.load(f)
            # 统计节点总数
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
    """异步保存服务器列表"""
    try:
        # 将数据传给顶层函数，避免闭包 Pickle 问题
        await run_in_bg_executor(_save_json_sync, config.CONFIG_FILE, state.SERVERS_CACHE)
    except Exception as e:
        logger.error(f"保存服务器失败: {e}")


async def save_subs():
    """异步保存订阅"""
    try:
        await run_in_bg_executor(_save_json_sync, config.SUBS_FILE, state.SUBS_CACHE)
    except Exception as e:
        logger.error(f"保存订阅失败: {e}")


async def save_nodes_cache():
    """异步保存节点数据缓存"""
    try:
        await run_in_bg_executor(_save_nodes_sync, config.NODES_CACHE_FILE, state.NODES_DATA)
    except Exception as e:
        logger.error(f"保存节点缓存失败: {e}")


async def save_admin_config():
    """保存管理员配置"""
    try:
        await run_in_bg_executor(_save_json_sync, config.ADMIN_CONFIG_FILE, state.ADMIN_CONFIG)
    except:
        pass


# ================= 2. 核心业务逻辑 =================

def calculate_dashboard_data():
    """计算仪表盘所需的各类统计数据"""
    try:
        # 1. 服务器在线统计
        total_servers = len(state.SERVERS_CACHE)
        online_servers = len([s for s in state.SERVERS_CACHE if s.get('_status') == 'online'])

        # 2. 节点与流量统计
        total_nodes = 0
        total_up = 0
        total_down = 0

        # 流量排行数据
        traffic_rank = []

        for srv in state.SERVERS_CACHE:
            url = srv.get('url')
            # 获取该服务器下的所有节点（API获取的 + 自定义的）
            api_nodes = state.NODES_DATA.get(url, []) or []
            custom_nodes = srv.get('custom_nodes', []) or []

            srv_up = 0
            srv_down = 0

            # 统计 API 节点
            for n in api_nodes:
                total_nodes += 1
                u = n.get('up', 0)
                d = n.get('down', 0)
                srv_up += u
                srv_down += d

            # 统计自定义节点 (通常无流量数据，但在列表中计数)
            total_nodes += len(custom_nodes)

            total_up += srv_up
            total_down += srv_down

            # 加入排行 (仅当有流量时)
            total_traffic = srv_up + srv_down
            if total_traffic > 0:
                traffic_rank.append({
                    'name': srv.get('name', 'Unknown'),
                    'value': round(total_traffic / 1024 / 1024 / 1024, 2)  # GB
                })

        # 3. 排序并截取前 10 名
        traffic_rank.sort(key=lambda x: x['value'], reverse=True)
        top_10 = traffic_rank[:10]

        bar_chart_data = {
            'names': [x['name'] for x in top_10],
            'values': [x['value'] for x in top_10]
        }

        # 4. 区域分布统计 (饼图)
        from collections import Counter
        region_cnt = Counter()
        for s in state.SERVERS_CACHE:
            # 尝试获取国旗或组名
            group = detect_country_group(s.get('name', ''), s)
            region_cnt[group] += 1

        pie_data = []
        for k, v in region_cnt.most_common(8):
            pie_data.append({'name': k, 'value': v})

        # 5. 格式化总流量
        total_traffic_bytes = total_up + total_down
        formatted_traffic = utils.format_bytes(total_traffic_bytes)

        return {
            "servers": f"{online_servers} / {total_servers}",
            "nodes": str(total_nodes),
            "traffic": formatted_traffic,
            "subs": str(len(state.SUBS_CACHE)),
            "bar_chart": bar_chart_data,
            "pie_chart": pie_data
        }
    except Exception as e:
        logger.error(f"仪表盘数据计算错误: {e}")
        return None


def detect_country_group(name, server_obj=None):
    """根据服务器名称或配置自动判断区域"""
    # 1. 优先检查 server_obj 中是否已有手动分组
    if server_obj and server_obj.get('group') and server_obj['group'] not in ['默认分组', '自动注册', '未分组']:
        return server_obj['group']

    # 2. 检查名称中的国旗
    for flag, country in config.AUTO_COUNTRY_MAP.items():
        if flag in name:
            return country

    # 3. 检查名称中的关键词 (不区分大小写)
    name_lower = name.lower()
    for key, country in config.AUTO_COUNTRY_MAP.items():
        if len(key) > 2 and key.lower() in name_lower:  # 忽略长度为2的简写，防止误判
            return country

    return "🏳️ 其他地区"


# ================= 3. 任务调度与后台执行 =================

async def run_in_bg_executor(func, *args):
    """在后台线程池中运行同步函数"""
    loop = asyncio.get_running_loop()
    if state.PROCESS_POOL is None:
        # 如果池未初始化，临时使用默认执行器
        return await loop.run_in_executor(None, func, *args)
    return await loop.run_in_executor(state.PROCESS_POOL, func, *args)


async def job_sync_all_traffic():
    """定时任务：同步所有 API 节点流量"""
    logger.info("🕒 [智能同步] 检查同步任务进度...")

    tasks = []
    for s in state.SERVERS_CACHE:
        # 仅同步配置了 API 的机器，且未安装探针的 (探针会主动推)
        if s.get('url') and not s.get('probe_installed'):
            tasks.append(fetch_inbounds_safe(s))

    if tasks:
        await asyncio.gather(*tasks)
        logger.info(f"✅ [智能同步] 完成 {len(tasks)} 个 API 节点的同步")
        # 保存一次缓存
        await save_nodes_cache()
        # 刷新 UI
        if state.refresh_dashboard_ui_func: await state.refresh_dashboard_ui_func()


async def job_monitor_status():
    """定时任务：简单的状态检查"""
    # 这里可以扩展更复杂的 TCP Ping，目前主要依赖探针推送更新状态
    pass


async def job_check_geo_ip():
    """后台任务：解析 IP 归属地并更新国旗"""
    import socket
    logger.info("🌍 [定时任务] 开始全量 IP 归属地检测与名称修正...")

    changed = False
    for s in state.SERVERS_CACHE:
        try:
            # 如果名字里没有国旗，且不是自定义分组
            if "🏳️" in s.get('name', '') or not any(c in s.get('name', '') for c in "🇨🇳🇺🇸🇭🇰🇯🇵🇰🇷🇸🇬"):
                # 获取 Host
                host = s.get('ssh_host') or s.get('url', '').replace('http://', '').replace('https://', '').split(':')[
                    0]
                if not host: continue

                # 解析 IP
                try:
                    ip = socket.gethostbyname(host)
                except:
                    continue

                # 获取 GeoInfo
                flag = await run_in_bg_executor(utils.get_flag_from_ip, ip)

                if flag and flag not in s['name']:
                    # 移除旧的白旗
                    clean_name = s['name'].replace("🏳️", "").strip()
                    s['name'] = f"{flag} {clean_name}"
                    s['group'] = detect_country_group(s['name'])
                    changed = True
        except:
            pass

    if changed:
        await save_servers()
        logger.info("✅ 名称检查完毕，已修正部分服务器国旗")
    else:
        logger.info("✅ 名称检查完毕，无需修正")


# ================= 4. 节点获取逻辑 =================

# [logic.py] 请替换原有的 fetch_inbounds_safe 函数
async def fetch_inbounds_safe(server_conf, force_refresh=False, sync_name=False):
    """
    获取节点的统一入口。
    1. 如果是 Root 模式，优先尝试 SSH 获取
    2. 如果是 API 模式，尝试 HTTP 获取
    3. 失败则返回缓存
    4. sync_name=True 时，会触发自动命名和自动分组
    """
    url = server_conf.get('url')

    # --- 自动命名与分组逻辑 (修复点) ---
    if sync_name:
        try:
            # 1. 解析真实 IP
            host = server_conf.get('ssh_host')
            if not host and url:
                host = url.replace('http://', '').replace('https://', '').split(':')[0]

            if host:
                # 2. 获取国旗
                import socket
                try:
                    # 如果是域名则解析
                    if not any(char.isdigit() for char in host):
                        host = socket.gethostbyname(host)
                except:
                    pass

                flag = await run_in_bg_executor(utils.get_flag_from_ip, host)

                # 3. 更新名称 (保留原有备注，增加国旗)
                old_name = server_conf.get('name', 'Server')
                # 如果名字里还没国旗，加上去
                if flag != "🏳️" and flag not in old_name:
                    clean_name = old_name.replace("🏳️", "").strip()
                    # 如果原名是 IP 或 URL，直接用 "国旗 国家" 格式
                    if clean_name == host or clean_name == url:
                        # 尝试获取国家名
                        pass  # 简单处理，直接加国旗
                    server_conf['name'] = f"{flag} {clean_name}"

                # 4. 自动更新分组
                new_group = detect_country_group(server_conf['name'], server_conf)
                if new_group != '🏳️ 其他地区':
                    server_conf['group'] = new_group

                await save_servers()
        except Exception as e:
            logger.error(f"自动同步名称失败: {e}")

    # 策略 A: 探针/SSH 模式 (通常不主动拉取，除非 force_refresh)
    if server_conf.get('probe_installed'):
        # 直接返回缓存 (探针数据已通过 push 接口写入缓存)
        return state.NODES_DATA.get(url, [])

    # 策略 B: API 模式
    if not url or not server_conf.get('user'):
        return []

    try:
        mgr = get_manager(server_conf)
        if not mgr: return []

        # 异步调用获取
        if hasattr(mgr, 'get_inbounds'):
            # 兼容同步和异步
            if asyncio.iscoroutinefunction(mgr.get_inbounds):
                nodes = await mgr.get_inbounds()
            else:
                nodes = await run_in_bg_executor(mgr.get_inbounds)

            if nodes:
                state.NODES_DATA[url] = nodes
                server_conf['_status'] = 'online'
                return nodes
    except Exception as e:
        logger.warning(f"获取节点失败 [{server_conf.get('name')}]: {e}")
        server_conf['_status'] = 'offline'

    return state.NODES_DATA.get(url, [])


def get_manager(server_conf):
    """工厂函数：根据配置返回对应的管理器实例 (XUI_API 或 XUI_SSH)"""
    # 1. 优先 SSH
    if server_conf.get('ssh_host') and server_conf.get('ssh_user'):
        from utils import XUI_SSH_Manager  # 避免循环引用，局部导入
        return XUI_SSH_Manager(server_conf)

    # 2. 其次 API
    if server_conf.get('url') and server_conf.get('user'):
        from utils import XUI_API_Manager
        return XUI_API_Manager(server_conf)

    return None


# ================= 5. 探针批量安装与辅助 =================

async def batch_install_all_probes():
    """为所有配置了 SSH 的服务器安装/更新探针"""
    from config import PROBE_INSTALL_SCRIPT
    success_count = 0

    # 获取本机 IP
    my_ip = "127.0.0.1"
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        my_ip = s.getsockname()[0]
        s.close()
    except:
        pass

    # 获取端口
    base_url = state.ADMIN_CONFIG.get('manager_base_url', f"http://{my_ip}:8080")

    final_script = PROBE_INSTALL_SCRIPT.replace("__MANAGER_URL__", base_url) \
        .replace("__TOKEN__", state.ADMIN_CONFIG.get('probe_token', 'default_token')) \
        .replace("__PING_CT__", state.ADMIN_CONFIG.get('ping_target_ct', '1.1.1.1')) \
        .replace("__PING_CU__", state.ADMIN_CONFIG.get('ping_target_cu', '1.1.1.1')) \
        .replace("__PING_CM__", state.ADMIN_CONFIG.get('ping_target_cm', '1.1.1.1'))

    for s in state.SERVERS_CACHE:
        if s.get('ssh_host'):
            try:
                utils.safe_notify(f"正在向 {s['name']} 推送探针...", "ongoing")
                ok, _ = await run_in_bg_executor(utils._ssh_exec_wrapper, s, final_script)
                if ok:
                    success_count += 1
                    s['probe_installed'] = True
            except:
                pass

    await save_servers()
    utils.safe_notify(f"批量更新完成: 成功 {success_count} 台", "positive")


async def force_geoip_naming_task(server_conf):
    """注册成功后，强制执行一次 GeoIP 命名"""
    await asyncio.sleep(2)  # 等待网络稳定
    try:
        host = server_conf.get('ssh_host') or server_conf.get('url', '').split('://')[-1].split(':')[0]
        flag = await run_in_bg_executor(utils.get_flag_from_ip, host)
        if flag:
            server_conf['name'] = f"{flag} {server_conf.get('name', '').replace('🏳️', '').strip()}"
            await save_servers()
    except:
        pass


# [logic.py] 替换原有的 pass
async def smart_detect_ssh_user_task(server_conf):
    """自动探测 SSH 用户名"""
    candidates = ['ubuntu', 'root', 'debian', 'opc', 'ec2-user', 'admin']
    ip = server_conf['url'].split('://')[-1].split(':')[0]
    
    logger.info(f"🕵️‍♂️ [智能探测] 开始探测 {server_conf['name']} ({ip}) ...")
    
    found_user = None
    for user in candidates:
        server_conf['ssh_user'] = user
        # 尝试连接
        client, msg = await run_in_bg_executor(utils.get_ssh_client_sync, server_conf)
        if client:
            client.close()
            found_user = user
            logger.info(f"✅ [智能探测] 成功匹配用户名: {user}")
            break
            
    if found_user:
        server_conf['ssh_user'] = found_user
        await save_servers()
        # 触发探针安装
        if state.ADMIN_CONFIG.get('probe_enabled', False):
            await asyncio.sleep(2)
            await batch_install_all_probes() # 这里可能会重复安装所有，建议优化为只安装单台
            # 或者调用: await install_probe_on_server(server_conf) # 需要将 install_probe_on_server 移到 logic.py
    else:
        logger.error(f"❌ [智能探测] {server_conf['name']} 失败")


def record_ping_history(url, pings):
    """记录 Ping 历史 (保留最近24小时)"""
    if url not in state.PING_TREND_CACHE:
        state.PING_TREND_CACHE[url] = []

    now = time.time()
    record = {
        'ts': now,
        'time_str': datetime.datetime.fromtimestamp(now).strftime('%H:%M'),
        'ct': pings.get('电信', -1),
        'cu': pings.get('联通', -1),
        'cm': pings.get('移动', -1)
    }
    state.PING_TREND_CACHE[url].append(record)

    # 清理过期数据 (保留 1440 个点 = 24小时 * 60分)
    if len(state.PING_TREND_CACHE[url]) > 1440:
        state.PING_TREND_CACHE[url] = state.PING_TREND_CACHE[url][-1440:]


# ================= 6. 备份/恢复逻辑 (顶层调用) =================

async def create_backup_zip():
    if not os.path.exists('backup'): os.makedirs('backup')
    zip_filename = f"backup/backup_{int(time.time())}.zip"

    # 调用顶层同步函数
    return await run_in_bg_executor(_zip_backup_sync, config.DATA_DIR, zip_filename)


async def restore_backup_zip(content_bytes):
    # 调用顶层同步函数
    res = await run_in_bg_executor(_unzip_backup_sync, content_bytes, config.DATA_DIR)
    if res:
        init_data()  # 重新加载内存
    return res

# [logic.py] 替换原有的 pass
async def job_monitor_status():
    """定时任务：服务器状态监控与报警"""
    # 限制并发
    sema = asyncio.Semaphore(50)
    FAILURE_THRESHOLD = 3
    current_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    
    # 定义报警缓存 (需要在 state.py 中添加 ALERT_CACHE = {} 和 FAILURE_COUNTS = {})
    if not hasattr(state, 'ALERT_CACHE'): state.ALERT_CACHE = {}
    if not hasattr(state, 'FAILURE_COUNTS'): state.FAILURE_COUNTS = {}

    async def _check_single_server(srv):
        # 仅监控已安装探针的机器
        if not srv.get('probe_installed', False): return

        async with sema:
            await asyncio.sleep(0.01)
            url = srv['url']
            name = srv.get('name', 'Unknown')
            
            # 获取状态
            res = await get_server_status(srv)
            is_online = (isinstance(res, dict) and res.get('status') == 'online')

            # TG 报警逻辑
            if not state.ADMIN_CONFIG.get('tg_bot_token'): return

            display_ip = url.split('://')[-1].split(':')[0]

            if is_online:
                state.FAILURE_COUNTS[url] = 0
                # 发送恢复通知
                if state.ALERT_CACHE.get(url) == 'offline':
                    msg = f"🟢 **恢复**\n🖥️ `{name}`\n🔗 `{display_ip}`\n🕒 `{current_time}`"
                    asyncio.create_task(send_telegram_message(msg))
                    state.ALERT_CACHE[url] = 'online'
            else:
                count = state.FAILURE_COUNTS.get(url, 0) + 1
                state.FAILURE_COUNTS[url] = count
                
                if count >= FAILURE_THRESHOLD:
                    if state.ALERT_CACHE.get(url) != 'offline':
                        msg = f"🔴 **离线报警**\n🖥️ `{name}`\n🔗 `{display_ip}`\n🕒 `{current_time}`"
                        asyncio.create_task(send_telegram_message(msg))
                        state.ALERT_CACHE[url] = 'offline'

    tasks = [_check_single_server(s) for s in state.SERVERS_CACHE]
    if tasks: await asyncio.gather(*tasks)

# 辅助函数：发送 TG (放在 logic.py 或 utils.py)
async def send_telegram_message(text):
    token = state.ADMIN_CONFIG.get('tg_bot_token')
    chat_id = state.ADMIN_CONFIG.get('tg_chat_id')
    if not token or not chat_id: return
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        await run_in_bg_executor(requests.post, url, {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}, timeout=5)
    except: pass

