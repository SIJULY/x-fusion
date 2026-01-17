import logging
import asyncio
import time
from nicegui import run, ui
from app.core.state import PROBE_DATA_CACHE, ADMIN_CONFIG, SERVERS_CACHE
from app.core.data_manager import save_servers
from app.services.ssh_service import get_ssh_client_sync

logger = logging.getLogger("ProbeService")

# ================= 探针安装脚本模板 =================
PROBE_INSTALL_SCRIPT = r"""
bash -c '
[ "$(id -u)" -eq 0 ] || { command -v sudo >/dev/null && exec sudo bash "$0" "$@"; echo "Root required"; exit 1; }

# 1. 安装基础依赖
if [ -f /etc/debian_version ]; then
    apt-get update -y >/dev/null 2>&1
    apt-get install -y python3 iputils-ping util-linux >/dev/null 2>&1
elif [ -f /etc/redhat-release ]; then
    yum install -y python3 iputils util-linux >/dev/null 2>&1
elif [ -f /etc/alpine-release ]; then
    apk add python3 iputils util-linux >/dev/null 2>&1
fi

# 2. 写入 Python 采集脚本
cat > /root/x_fusion_agent.py << "PYTHON_EOF"
import time, json, os, socket, sys, subprocess, re, platform
import urllib.request, urllib.error
import ssl

MANAGER_URL = "__MANAGER_URL__/api/probe/push"
TOKEN = "__TOKEN__"
SERVER_URL = "__SERVER_URL__"

PING_TARGETS = {
"电信": "__PING_CT__",
"联通": "__PING_CU__",
"移动": "__PING_CM__"
}

ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE

def get_network_bytes():
    r, t = 0, 0
    try:
        with open("/proc/net/dev") as f:
            lines = f.readlines()[2:]
            for l in lines:
                cols = l.split(":")
                if len(cols)<2: continue
                parts = cols[1].split()
                if len(parts)>=9 and cols[0].strip() != "lo":
                    r += int(parts[0])
                    t += int(parts[8])
    except: pass
    return r, t

def get_info():
    global SERVER_URL
    data = {"token": TOKEN, "static": {}}

    if not SERVER_URL:
        try:
            with urllib.request.urlopen("http://checkip.amazonaws.com", timeout=5, context=ssl_ctx) as r:
                my_ip = r.read().decode().strip()
                SERVER_URL = "http://" + my_ip + ":54322"
        except: pass
    data["server_url"] = SERVER_URL

    try:
        # 第一次采样
        net_in_1, net_out_1 = get_network_bytes()
        with open("/proc/stat") as f:
            fs = [float(x) for x in f.readline().split()[1:5]]
            tot1, idle1 = sum(fs), fs[3]

        time.sleep(1)

        # 第二次采样
        net_in_2, net_out_2 = get_network_bytes()
        with open("/proc/stat") as f:
            fs = [float(x) for x in f.readline().split()[1:5]]
            tot2, idle2 = sum(fs), fs[3]

        data["cpu_usage"] = round((1 - (idle2-idle1)/(tot2-tot1)) * 100, 1)
        data["cpu_cores"] = os.cpu_count() or 1

        data["net_speed_in"] = net_in_2 - net_in_1
        data["net_speed_out"] = net_out_2 - net_out_1
        data["net_total_in"] = net_in_2
        data["net_total_out"] = net_out_2

        with open("/proc/loadavg") as f: data["load_1"] = float(f.read().split()[0])

        with open("/proc/meminfo") as f:
            m = {}
            for l in f:
                p = l.split()
                if len(p)>=2: m[p[0].rstrip(":")] = int(p[1])

        tot = m.get("MemTotal", 1)
        avail = m.get("MemAvailable", m.get("MemFree", 0))
        data["mem_total"] = round(tot/1024/1024, 2)
        data["mem_usage"] = round(((tot-avail)/tot)*100, 1)
        data["mem_used"] = tot - avail
        data["swap_total"] = round(m.get("SwapTotal", 0)/1024/1024, 2)
        data["swap_free"] = round(m.get("SwapFree", 0)/1024/1024, 2)

        st = os.statvfs("/")
        data["disk_total"] = round((st.f_blocks * st.f_frsize)/1024/1024/1024, 2)
        free = st.f_bavail * st.f_frsize
        total = st.f_blocks * st.f_frsize
        data["disk_usage"] = round(((total-free)/total)*100, 1)
        data["disk_used"] = total - free

        with open("/proc/uptime") as f: u = float(f.read().split()[0])
        d = int(u // 86400); h = int((u % 86400) // 3600); m = int((u % 3600) // 60)
        data["uptime"] = "%d天 %d时 %d分" % (d, h, m)

        # 获取静态信息缓存
        try:
            if os.path.exists("/etc/os-release"):
                with open("/etc/os-release") as f:
                    for line in f:
                        if line.startswith("PRETTY_NAME="):
                            data["static"]["os"] = line.split("=")[1].strip().strip("\"")
                            break
        except: pass

        data["static"]["arch"] = platform.machine()

        data["pings"] = {}
        for k, v in PING_TARGETS.items():
            try:
                ip = v.split("://")[-1].split(":")[0]
                cmd = "ping -c 1 -W 1 " + ip
                res = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                if res.returncode == 0:
                    match = re.search(r"time=([\d.]+)", res.stdout.decode())
                    if match: data["pings"][k] = int(float(match.group(1)))
            except: pass

    except: pass
    return data

def push():
    while True:
        try:
            js = json.dumps(get_info()).encode("utf-8")
            req = urllib.request.Request(MANAGER_URL, data=js, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10, context=ssl_ctx) as r: pass
        except: pass
        time.sleep(1)

if __name__ == "__main__":
    push()
PYTHON_EOF

# 3. 创建系统服务
cat > /etc/systemd/system/x-fusion-agent.service << SERVICE_EOF
[Unit]
Description=X-Fusion Probe Agent
After=network.target

[Service]
Type=simple
User=root
ExecStart=/usr/bin/python3 /root/x_fusion_agent.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE_EOF

# 4. 启动服务
systemctl daemon-reload
systemctl enable x-fusion-agent
systemctl restart x-fusion-agent
exit 0
'
"""


# ================= 单台安装函数 =================
async def install_probe_on_server(server_conf):
    name = server_conf.get('name', 'Unknown')
    my_token = ADMIN_CONFIG.get('probe_token', 'default_token')

    # 获取主控端地址
    manager_url = ADMIN_CONFIG.get('manager_base_url', 'http://xui-manager:8080')

    # 获取测速目标
    ping_ct = ADMIN_CONFIG.get('ping_target_ct', '202.102.192.68')
    ping_cu = ADMIN_CONFIG.get('ping_target_cu', '112.122.10.26')
    ping_cm = ADMIN_CONFIG.get('ping_target_cm', '211.138.180.2')

    # 替换脚本变量
    real_script = PROBE_INSTALL_SCRIPT \
        .replace("__MANAGER_URL__", manager_url) \
        .replace("__TOKEN__", my_token) \
        .replace("__SERVER_URL__", server_conf['url']) \
        .replace("__PING_CT__", ping_ct) \
        .replace("__PING_CU__", ping_cu) \
        .replace("__PING_CM__", ping_cm)

    # SSH 执行封装
    def _do_install():
        client = None
        try:
            client, msg = get_ssh_client_sync(server_conf)
            if not client: return False, f"SSH连接失败: {msg}"

            # 执行安装 (设置 60秒超时)
            stdin, stdout, stderr = client.exec_command(real_script, timeout=60)
            exit_status = stdout.channel.recv_exit_status()

            client.close()
            if exit_status == 0: return True, "Agent 安装成功并启动"
            return False, f"安装脚本错误 (Exit {exit_status})"
        except Exception as e:
            return False, f"异常: {str(e)}"

    # 异步执行
    success, msg = await run.io_bound(_do_install)

    if success:
        server_conf['probe_installed'] = True
        await save_servers()
        logger.info(f"✅ [Push Agent] {name} 部署成功")
    else:
        logger.warning(f"⚠️ [Push Agent] {name} 部署失败: {msg}")

    return success


# ================= ✨✨✨ [补全] 批量安装函数 ✨✨✨ =================
async def batch_install_all_probes():
    """批量并发安装所有探针"""
    if not SERVERS_CACHE:
        return

    # 限制并发数 (例如同时只连 10 台，防止带宽/CPU爆炸)
    sema = asyncio.Semaphore(10)

    async def _worker(server_conf):
        async with sema:
            await install_probe_on_server(server_conf)

    # 创建所有任务
    tasks = [_worker(s) for s in SERVERS_CACHE]

    # 等待执行完成
    if tasks:
        await asyncio.gather(*tasks)


# ================= 获取状态函数 =================
async def get_server_status(server_conf):
    """
    获取服务器状态：
    1. 优先从探针缓存读取 (如果安装了探针)
    2. 如果探针超时 > 15秒，视为离线
    """
    raw_url = server_conf['url']

    # 只有当服务器安装了 Python 探针脚本，才从缓存读取数据
    if server_conf.get('probe_installed', False) or raw_url in PROBE_DATA_CACHE:
        cache = PROBE_DATA_CACHE.get(raw_url)
        if cache:
            # 检查数据新鲜度 (15秒超时)
            if time.time() - cache.get('last_updated', 0) < 15:
                return cache
            else:
                return {'status': 'offline', 'msg': '探针离线 (超时)'}

    # 如果没装探针，直接返回离线
    return {'status': 'offline', 'msg': '未安装探针'}