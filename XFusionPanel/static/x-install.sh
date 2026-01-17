#!/bin/bash
# X-Fusion Agent 一键安装脚本
# 用法: curl -sL http://your-panel/static/x-install.sh | bash -s -- "TOKEN" "REGISTER_API_URL" "CT_IP" "CU_IP" "CM_IP"

export DEBIAN_FRONTEND=noninteractive

# 1. 接收参数
TOKEN="$1"
REGISTER_URL="$2"
PING_CT="${3:-202.102.192.68}"
PING_CU="${4:-112.122.10.26}"
PING_CM="${5:-211.138.180.2}"

if [ -z "$TOKEN" ] || [ -z "$REGISTER_URL" ]; then
    echo "Error: Missing arguments (Token or URL)."
    exit 1
fi

# 2. 权限检查
if [ "$(id -u)" -ne 0 ]; then
    echo "Error: This script must be run as root."
    exit 1
fi

echo "Installing X-Fusion Agent..."

# 3. 安装依赖
if [ -f /etc/debian_version ]; then
    apt-get update -y >/dev/null 2>&1
    apt-get install -y python3 iputils-ping util-linux sqlite3 curl >/dev/null 2>&1
elif [ -f /etc/redhat-release ]; then
    yum install -y python3 iputils util-linux sqlite3 curl >/dev/null 2>&1
elif [ -f /etc/alpine-release ]; then
    apk add python3 iputils util-linux sqlite3 curl >/dev/null 2>&1
fi

# 4. 主动注册 (获取真实 Server URL)
# 从 REGISTER_URL (http://ip:port/api/probe/register) 提取 Base URL
# 并告诉面板 "我来了"，面板会根据 IP 自动创建或合并机器
echo "Registering to panel..."
REG_JSON="{\"token\": \"$TOKEN\"}"
curl -s -X POST -H "Content-Type: application/json" -d "$REG_JSON" "$REGISTER_URL" >/dev/null

# 提取主控端推送地址 (将 /register 替换为 /push)
# 例如: http://1.2.3.4:8080/api/probe/register -> http://1.2.3.4:8080/api/probe/push
PUSH_URL="${REGISTER_URL/register/push}"

# 5. 写入 Python 探针脚本
cat > /root/x_fusion_agent.py <<EOF
import time, json, os, socket, sys, subprocess, re, platform, sqlite3
import urllib.request, urllib.error
import ssl

MANAGER_URL = "$PUSH_URL"
TOKEN = "$TOKEN"

PING_TARGETS = {
    "电信": "$PING_CT",
    "联通": "$PING_CU",
    "移动": "$PING_CM"
}

ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE

def get_cpu_model():
    model = "Unknown"
    try:
        # Try lscpu
        try:
            out = subprocess.check_output("lscpu", shell=True).decode()
            for line in out.split("\n"):
                if "Model name:" in line: return line.split(":")[1].strip()
        except: pass
        # Try /proc/cpuinfo
        with open("/proc/cpuinfo", "r") as f:
            for line in f:
                if "model name" in line: return line.split(":")[1].strip()
                if "Hardware" in line: return line.split(":")[1].strip()
    except: pass
    return model

def get_os_distro():
    try:
        if os.path.exists("/etc/os-release"):
            with open("/etc/os-release") as f:
                for line in f:
                    if line.startswith("PRETTY_NAME="):
                        return line.split("=")[1].strip().strip("\"")
    except: pass
    try: return platform.platform()
    except: return "Linux (Unknown)"

STATIC_CACHE = {
    "cpu_model": get_cpu_model(),
    "arch": platform.machine(),
    "os": get_os_distro(),
    "virt": "Unknown"
}
try:
    v = subprocess.check_output("systemd-detect-virt", shell=True).decode().strip()
    if v and v != "none": STATIC_CACHE["virt"] = v
except: pass

def get_ping(target):
    try:
        ip = target.split("://")[-1].split(":")[0]
        # Linux ping timeout logic
        cmd = "ping -c 1 -W 1 " + ip
        res = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode == 0:
            match = re.search(r"time=([\d.]+)", res.stdout.decode())
            if match: return int(float(match.group(1)))
    except: pass
    return -1

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

def get_xui_rows():
    # Detect typical X-UI DB paths
    db_paths = [
        "/etc/x-ui/x-ui.db",
        "/usr/local/x-ui/bin/x-ui.db",
        "/usr/local/x-ui/x-ui.db"
    ]
    db_path = next((p for p in db_paths if os.path.exists(p)), None)
    if not db_path: return None

    try:
        # Use Read-Only mode uri
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cursor = conn.cursor()
        cursor.execute("SELECT id, up, down, total, remark, enable, protocol, port, settings, stream_settings, expiry_time, listen FROM inbounds")
        rows = cursor.fetchall()
        inbounds = []
        for row in rows:
            inbounds.append({
                "id": row[0], "up": row[1], "down": row[2], "total": row[3],
                "remark": row[4], "enable": True if row[5] == 1 else False,
                "protocol": row[6], "port": row[7],
                "settings": row[8], "streamSettings": row[9],
                "expiryTime": row[10], "listen": row[11]
            })
        conn.close()
        return inbounds
    except: return None

def get_info():
    data = {"token": TOKEN, "static": STATIC_CACHE, "server_url": "$REGISTER_URL"} # Echo back for ID

    try:
        net_in_1, net_out_1 = get_network_bytes()
        with open("/proc/stat") as f:
            fs = [float(x) for x in f.readline().split()[1:5]]
            tot1, idle1 = sum(fs), fs[3]

        time.sleep(1)

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
        data["swap_total"] = round(m.get("SwapTotal", 0)/1024/1024, 2)
        data["swap_free"] = round(m.get("SwapFree", 0)/1024/1024, 2)

        st = os.statvfs("/")
        data["disk_total"] = round((st.f_blocks * st.f_frsize)/1024/1024/1024, 2)
        free = st.f_bavail * st.f_frsize
        total = st.f_blocks * st.f_frsize
        data["disk_usage"] = round(((total-free)/total)*100, 1)

        with open("/proc/uptime") as f: u = float(f.read().split()[0])
        d = int(u // 86400); h = int((u % 86400) // 3600); m = int((u % 3600) // 60)
        data["uptime"] = "%d days, %02d:%02d" % (d, h, m)

        data["pings"] = {k: get_ping(v) for k, v in PING_TARGETS.items()}

        xui = get_xui_rows()
        if xui is not None: data["xui_data"] = xui

    except: pass
    return data

def push():
    while True:
        try:
            js = json.dumps(get_info()).encode("utf-8")
            req = urllib.request.Request(MANAGER_URL, data=js, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10, context=ssl_ctx) as r: pass
        except: pass
        time.sleep(2) # 2秒上报一次，实现秒级监控

if __name__ == "__main__":
    push()
EOF

# 6. 配置 Systemd 服务
cat > /etc/systemd/system/x-fusion-agent.service <<SERVICE_EOF
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

# 7. 启动
systemctl daemon-reload
systemctl enable x-fusion-agent
systemctl restart x-fusion-agent

echo "✅ Installation Complete. Agent is running."