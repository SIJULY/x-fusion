# services/geoip.py
import requests
import asyncio
import logging
import re
from nicegui import run
from core.state import IP_GEO_CACHE, SERVERS_CACHE
from core.config import AUTO_COUNTRY_MAP
from core.storage import save_servers

logger = logging.getLogger("Services.GeoIP")


# ================= 从 IP 获取地理信息 =================
def fetch_geo_from_ip(host):
    try:
        clean_host = host.split('://')[-1].split(':')[0]
        # 跳过内网
        if clean_host.startswith('192.168.') or clean_host.startswith('10.') or clean_host == '127.0.0.1':
            return None
        if clean_host in IP_GEO_CACHE:
            return IP_GEO_CACHE[clean_host]

        # 请求 ip-api
        with requests.Session() as s:
            url = f"http://ip-api.com/json/{clean_host}?lang=zh-CN&fields=status,lat,lon,country"
            r = s.get(url, timeout=3)
            if r.status_code == 200:
                data = r.json()
                if data.get('status') == 'success':
                    result = (data['lat'], data['lon'], data['country'])
                    IP_GEO_CACHE[clean_host] = result
                    return result
    except:
        pass
    return None


# ================= 获取国旗字符串 =================
def get_flag_for_country(country_name):
    if not country_name: return "🏳️ 未知"

    # 1. 正向匹配 Key
    for k, v in AUTO_COUNTRY_MAP.items():
        if k.upper() == country_name.upper() or k in country_name:
            return v

            # 2. 反向匹配 Value (中文匹配)
    for v in AUTO_COUNTRY_MAP.values():
        if country_name in v:
            return v

    return f"🏳️ {country_name}"


# ================= 核心：智能检测分组 (缺失的函数) =================
def detect_country_group(name, server_config=None):
    """
    根据服务器名称或配置，智能判断所属国家分组
    """
    # 1. 优先使用手动保存的分组
    if server_config:
        saved_group = server_config.get('group')
        # 排除无效分组
        if saved_group and saved_group not in ['默认分组', '自动注册', '未分组', '自动导入', '🏳️ 其他地区', '其他地区']:
            # 尝试标准化 (如输入 "美国" -> "🇺🇸 美国")
            for v in AUTO_COUNTRY_MAP.values():
                if saved_group in v or v in saved_group:
                    return v
            return saved_group

    # 2. 关键词匹配 (倒序匹配，优先匹配长词)
    name_upper = name.upper()
    sorted_keys = sorted(AUTO_COUNTRY_MAP.keys(), key=len, reverse=True)

    for key in sorted_keys:
        val = AUTO_COUNTRY_MAP[key]

        if key in name_upper:
            # 针对 2-3 位短字母缩写 (如 CL, US, SG, ID) 进行边界检查
            if len(key) <= 3 and key.isalpha():
                # 正则：(?<![A-Z0-9]) 表示前面不能是字母数字
                #       (?![A-Z0-9])  表示后面不能是字母数字
                pattern = r'(?<![A-Z0-9])' + re.escape(key) + r'(?![A-Z0-9])'
                if re.search(pattern, name_upper):
                    return val
            else:
                # 长关键字 (Japan) 或 Emoji (🇯🇵) 或带符号的 (HK-)，直接匹配
                return val

    # 3. 检查 IP 检测的隐藏字段
    if server_config and server_config.get('_detected_region'):
        detected = server_config['_detected_region']
        flag_group = get_flag_for_country(detected)
        if "🏳️" not in flag_group:
            return flag_group

    return '🏳️ 其他地区'


# ================= 自动添加国旗 =================
async def auto_prepend_flag(name, url):
    if not name: return name
    for v in AUTO_COUNTRY_MAP.values():
        flag_icon = v.split(' ')[0]
        if flag_icon in name: return name

    try:
        geo_info = await run.io_bound(fetch_geo_from_ip, url)
        if not geo_info: return name

        country_name = geo_info[2]
        flag_group = get_flag_for_country(country_name)
        flag_icon = flag_group.split(' ')[0]

        if flag_icon in name: return name
        return f"{flag_icon} {name}"
    except:
        return name


# ================= 强制 GeoIP 命名任务 =================
async def force_geoip_naming_task(server_conf, max_retries=10):
    """强制执行 GeoIP 解析，修改服务器名称和分组"""
    url = server_conf['url']
    logger.info(f"🌍 [GeoIP] 开始处理: {url}")

    for i in range(max_retries):
        try:
            geo_info = await run.io_bound(fetch_geo_from_ip, url)
            if geo_info:
                country_raw = geo_info[2]
                flag_group = get_flag_for_country(country_raw)

                # 计算序号
                count = 1
                for s in SERVERS_CACHE:
                    if s is not server_conf and s.get('name', '').startswith(flag_group):
                        count += 1

                final_name = f"{flag_group}-{count}"
                old_name = server_conf.get('name', '')

                if old_name != final_name:
                    server_conf['name'] = final_name
                    server_conf['group'] = flag_group
                    server_conf['_detected_region'] = country_raw

                    await save_servers()
                    logger.info(f"✅ [GeoIP] 修正: {old_name} -> {final_name}")
                    return
            await asyncio.sleep(3)
        except Exception as e:
            logger.error(f"❌ [GeoIP] 异常: {e}")
            await asyncio.sleep(3)


# ================= 定时任务：IP 检查 =================
async def job_check_geo_ip():
    logger.info("🌍 [定时任务] 开始全量 IP 归属地检测与名称修正...")
    data_changed = False

    known_flags = []
    for val in AUTO_COUNTRY_MAP.values():
        icon = val.split(' ')[0]
        if icon and icon not in known_flags: known_flags.append(icon)

    for s in SERVERS_CACHE:
        old_name = s.get('name', '')
        new_name = old_name

        # 清洗白旗
        if new_name.startswith('🏳️ ') or new_name.startswith('🏳️'):
            if len(new_name) > 2: new_name = new_name.replace('🏳️', '').strip()

        # 检查国旗
        has_flag = any(flag in new_name for flag in known_flags)

        if not has_flag:
            try:
                geo = await run.io_bound(fetch_geo_from_ip, s['url'])
                if geo:
                    s['lat'] = geo[0];
                    s['lon'] = geo[1];
                    s['_detected_region'] = geo[2]
                    flag_prefix = get_flag_for_country(geo[2])
                    flag_icon = flag_prefix.split(' ')[0]
                    if flag_icon and flag_icon not in new_name:
                        new_name = f"{flag_icon} {new_name}"
            except:
                pass

        if new_name != old_name:
            s['name'] = new_name
            data_changed = True

    if data_changed:
        await save_servers()