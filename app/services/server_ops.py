import logging
import asyncio
import re
from nicegui import run
from app.core.state import SERVERS_CACHE, ADMIN_CONFIG
from app.core.data_manager import save_servers
from app.services.xui_client import get_manager, run_in_bg_executor
from app.utils.geo_ip import fetch_geo_from_ip, get_flag_for_country, auto_prepend_flag
from app.services.ssh_service import get_ssh_client_sync
from app.services.probe import install_probe_on_server

logger = logging.getLogger("ServerOps")

# ================= 智能排序辅助函数 (补全部分) =================

CN_NUM_MAP = {'〇': 0, '零': 0, '一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}


def cn_to_arabic_str(match):
    s = match.group()
    if not s: return s
    if '十' in s:
        val = 0
        parts = s.split('十')
        if parts[0]:
            val += CN_NUM_MAP.get(parts[0], 0) * 10
        else:
            val += 10
        if len(parts) > 1 and parts[1]: val += CN_NUM_MAP.get(parts[1], 0)
        return str(val)
    return "".join(str(CN_NUM_MAP.get(c, 0)) for c in s)


def to_safe_sort_list(items):
    """确保列表可排序：[(权重, 值), ...]"""
    safe_list = []
    for item in items:
        if isinstance(item, int):
            safe_list.append((1, item))  # 数字权重高
        else:
            safe_list.append((0, str(item).lower()))
    return safe_list


def smart_sort_key(server_info):
    """智能排序键生成函数"""
    name = server_info.get('name', '')
    if not name: return []

    # 1. 预处理：汉字转数字
    try:
        name_normalized = re.sub(r'[零一二三四五六七八九十]+', cn_to_arabic_str, name)
    except:
        name_normalized = name

    # 2. 尝试智能拆分 (名称格式：Flag Area-Name-Num)
    try:
        if '|' in name_normalized:
            parts = name_normalized.split('|', 1)
            p1 = parts[0].strip();
            rest = parts[1].strip()
        else:
            p1 = name_normalized;
            rest = ""

        p2 = ""
        if ' ' in rest:
            parts = rest.split(' ', 1)
            p2 = parts[0].strip();
            rest = parts[1].strip()

        sub_parts = rest.split('-')
        p3 = sub_parts[0].strip()

        p3_num = 0;
        p3_text = p3
        p3_match = re.search(r'(\d+)$', p3)
        if p3_match:
            p3_num = int(p3_match.group(1))
            p3_text = p3[:p3_match.start()]

        p4 = "";
        p5 = 0
        if len(sub_parts) >= 2: p4 = sub_parts[1].strip()
        if len(sub_parts) >= 3:
            last = sub_parts[-1].strip()
            if last.isdigit():
                p5 = int(last)
            else:
                p4 += f"-{last}"
        elif len(sub_parts) == 2 and sub_parts[1].strip().isdigit():
            p5 = int(sub_parts[1].strip())

        return to_safe_sort_list([p1, p2, p3_text, p3_num, p4, p5])

    except:
        # 兜底：简单的数字混合排序
        parts = re.split(r'(\d+)', name_normalized)
        mixed_list = [int(text) if text.isdigit() else text for text in parts]
        return to_safe_sort_list(mixed_list)


# ================= 核心业务逻辑 =================

async def generate_smart_name(server_conf):
    """智能生成名称 (尝试读取面板备注 -> GeoIP)"""
    # 1. 尝试面板
    try:
        mgr = get_manager(server_conf)
        inbounds = await run_in_bg_executor(mgr.get_inbounds)
        if inbounds and len(inbounds) > 0:
            for node in inbounds:
                if node.get('remark'):
                    return await auto_prepend_flag(node['remark'], server_conf['url'])
    except:
        pass

    # 2. 尝试 GeoIP
    try:
        geo_info = await run.io_bound(fetch_geo_from_ip, server_conf['url'])
        if geo_info:
            country_name = geo_info[2]
            flag_prefix = get_flag_for_country(country_name)
            count = 1
            for s in SERVERS_CACHE:
                if s.get('name', '').startswith(flag_prefix): count += 1
            return f"{flag_prefix}-{count}"
    except:
        pass

    return f"Server-{len(SERVERS_CACHE) + 1}"


async def force_geoip_naming_task(server_conf, max_retries=10):
    """后台任务：强制 GeoIP 命名修正"""
    url = server_conf['url']
    logger.info(f"🌍 [命名修正] 开始处理: {url}")

    for i in range(max_retries):
        try:
            geo_info = await run.io_bound(fetch_geo_from_ip, url)
            if geo_info:
                country_raw = geo_info[2]
                flag_group = get_flag_for_country(country_raw)

                count = 1
                for s in SERVERS_CACHE:
                    if s is not server_conf and s.get('name', '').startswith(flag_group): count += 1

                final_name = f"{flag_group}-{count}"
                old_name = server_conf.get('name', '')

                if old_name != final_name:
                    server_conf['name'] = final_name
                    server_conf['group'] = flag_group
                    server_conf['_detected_region'] = country_raw
                    await save_servers()
                    logger.info(f"✅ [命名修正] 成功: {old_name} -> {final_name}")
                    return

        except Exception as e:
            logger.error(f"❌ 异常: {e}")
        await asyncio.sleep(3)


async def smart_detect_ssh_user_task(server_conf):
    """后台任务：智能探测 SSH 用户名"""
    candidates = ['ubuntu', 'root', 'ec2-user', 'debian', 'opc']
    ip = server_conf['url'].split('://')[-1].split(':')[0]
    original_user = server_conf.get('ssh_user', '')

    logger.info(f"🕵️‍♂️ [智能探测] 开始探测 {server_conf['name']} ({ip})")
    found_user = None

    for user in candidates:
        server_conf['ssh_user'] = user
        client, msg = await run.io_bound(get_ssh_client_sync, server_conf)
        if client:
            client.close()
            found_user = user
            logger.info(f"✅ [智能探测] 成功匹配: {user}")
            break

    if found_user:
        server_conf['ssh_user'] = found_user
        server_conf['_ssh_verified'] = True
        await save_servers()
        if ADMIN_CONFIG.get('probe_enabled', False):
            logger.info(f"🚀 [自动部署] SSH 验证通过，开始安装探针...")
            await asyncio.sleep(2)
            await install_probe_on_server(server_conf)
    else:
        logger.error(f"❌ [智能探测] 失败")
        if original_user: server_conf['ssh_user'] = original_user
        await save_servers()