# services/ping.py
import socket
import time
import asyncio
from nicegui import run
from core.state import PROCESS_POOL, PING_CACHE, PING_TREND_CACHE, DNS_CACHE, DNS_WAITING_LABELS


# ================= 全局 同步 Ping 函数 (由进程池调用) =================
def sync_ping_worker(host, port):
    try:
        start = time.time()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)  # 3秒超时
        sock.connect((host, int(port)))
        sock.close()
        return int((time.time() - start) * 1000)
    except:
        return -1


# ================= 异步批量 Ping =================
async def batch_ping_nodes(nodes, raw_host):
    """
    使用多进程池并行 Ping，彻底解放主线程。
    """
    if not PROCESS_POOL: return

    loop = asyncio.get_running_loop()

    # 1. 准备任务列表
    targets = []
    for n in nodes:
        host = n.get('listen')
        if not host or host == '0.0.0.0': host = raw_host
        port = n.get('port')
        key = f"{host}:{port}"
        targets.append((host, port, key))

    # 2. 定义回调处理
    async def run_single_ping(t_host, t_port, t_key):
        try:
            latency = await loop.run_in_executor(PROCESS_POOL, sync_ping_worker, t_host, t_port)
            PING_CACHE[t_key] = latency
        except:
            PING_CACHE[t_key] = -1

    # 3. 并发分发
    tasks = [run_single_ping(h, p, k) for h, p, k in targets]
    if tasks:
        await asyncio.gather(*tasks)


# ================= 记录 Ping 历史 (用于图表) =================
def record_ping_history(url, pings_dict):
    """
    记录探针返回的三网 Ping 数据到历史缓存
    """
    if not url or not pings_dict: return

    current_ts = time.time()

    # 初始化
    if url not in PING_TREND_CACHE:
        PING_TREND_CACHE[url] = []

    # 防抖：每分钟只记录一次
    if PING_TREND_CACHE[url]:
        last_record = PING_TREND_CACHE[url][-1]
        if current_ts - last_record['ts'] < 60:
            return

    import datetime
    time_str = datetime.datetime.fromtimestamp(current_ts).strftime('%m/%d %H:%M')

    ct = pings_dict.get('电信', 0);
    ct = ct if ct > 0 else 0
    cu = pings_dict.get('联通', 0);
    cu = cu if cu > 0 else 0
    cm = pings_dict.get('移动', 0);
    cm = cm if cm > 0 else 0

    PING_TREND_CACHE[url].append({
        'ts': current_ts,
        'time_str': time_str,
        'ct': ct,
        'cu': cu,
        'cm': cm
    })

    # 保留最近 1000 条
    if len(PING_TREND_CACHE[url]) > 1000:
        PING_TREND_CACHE[url] = PING_TREND_CACHE[url][-1000:]


# ================= ✨✨✨ [补全] DNS 解析相关逻辑 ✨✨✨ =================

async def _resolve_dns_bg(host):
    """后台线程池解析 DNS，解析完自动刷新所有绑定的 UI 标签"""
    try:
        # 放到后台线程去跑，绝对不卡主界面
        ip = await run.io_bound(socket.gethostbyname, host)
        DNS_CACHE[host] = ip

        # 通知前台 UI 更新
        if host in DNS_WAITING_LABELS:
            for label in DNS_WAITING_LABELS[host]:
                try:
                    if not label.is_deleted:
                        label.set_text(ip)  # 瞬间变成 IP
                except:
                    pass

            # 通知完了就清空，释放内存
            del DNS_WAITING_LABELS[host]

    except:
        DNS_CACHE[host] = "failed"  # 标记失败，防止反复解析


def get_real_ip_display(url):
    """
    非阻塞获取 IP：
    1. 有缓存 -> 直接返回 IP
    2. 没缓存 -> 先返回域名，同时偷偷启动后台解析任务
    """
    try:
        # 提取域名/IP
        host = url.split('://')[-1].split(':')[0]

        # 1. 如果本身就是 IP，直接返回
        import re
        if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", host):
            return host

        # 2. 查缓存
        if host in DNS_CACHE:
            val = DNS_CACHE[host]
            return val if val != "failed" else host

        # 3. 没缓存？(系统刚启动)
        # 启动后台任务，并立即返回域名占位
        asyncio.create_task(_resolve_dns_bg(host))
        return host

    except:
        return url


def bind_ip_label(url, label):
    """
    将 UI Label 绑定到 DNS 监听列表，解析完成后自动更新文字
    """
    try:
        host = url.split('://')[-1].split(':')[0]
        # 如果已经解析过，或者本身是 IP，就不需要监听了
        import re
        if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", host): return
        if host in DNS_CACHE: return

        # 加入监听列表
        if host not in DNS_WAITING_LABELS: DNS_WAITING_LABELS[host] = []
        DNS_WAITING_LABELS[host].append(label)
    except:
        pass