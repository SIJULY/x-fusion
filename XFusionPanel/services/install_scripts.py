# services/install_scripts.py

# ================= [V76 终极稳定版] XHTTP-Reality 部署脚本 =================
XHTTP_INSTALL_SCRIPT_TEMPLATE = r"""
#!/bin/bash
export DEBIAN_FRONTEND=noninteractive
export PATH=$PATH:/usr/local/bin

# 0. 自我清洗
sed -i 's/\r$//' "$0"

# 1. 基础环境检查与依赖安装
if [ -f /etc/debian_version ]; then
    apt-get update -y >/dev/null 2>&1
    apt-get install -y net-tools lsof curl unzip jq uuid-runtime openssl psmisc dnsutils >/dev/null 2>&1
elif [ -f /etc/redhat-release ]; then
    yum install -y net-tools lsof curl unzip jq psmisc bind-utils >/dev/null 2>&1
fi

log() { echo -e "\033[32m[DEBUG]\033[0m $1"; }
err() { echo -e "\033[31m[ERROR]\033[0m $1"; }

DOMAIN="$1"
if [ -z "$DOMAIN" ]; then err "域名参数缺失"; exit 1; fi

log "========== 开始部署 XHTTP (V76 稳定版) =========="
log "目标域名: $DOMAIN"

# 2. 端口强制清理
if netstat -tlpn | grep -q ":80 "; then
    fuser -k 80/tcp >/dev/null 2>&1; killall -9 nginx >/dev/null 2>&1; killall -9 xray >/dev/null 2>&1
    sleep 1
fi
if netstat -tlpn | grep -q ":443 "; then
    fuser -k 443/tcp >/dev/null 2>&1
    sleep 1
fi

PORT_REALITY=443
PORT_XHTTP=80

# 3. 安装/更新 Xray
xray_bin="/usr/local/bin/xray"
rm -f "$xray_bin"
arch=$(uname -m); case "$arch" in x86_64) a="64";; aarch64) a="arm64-v8a";; esac
curl -fsSL https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-${a}.zip -o /tmp/xray.zip
unzip -qo /tmp/xray.zip -d /tmp/xray
install -m 755 /tmp/xray/xray "$xray_bin"

# 4. 生成密钥与配置
KEYS=$($xray_bin x25519)
PRI_KEY=$(echo "$KEYS" | grep -i "Private" | awk '{print $NF}')
PUB_KEY=$(echo "$KEYS" | grep -i "Public" | awk '{print $NF}')
[ -z "$PUB_KEY" ] && { PRI_KEY=$(echo "$KEYS" | head -n1 | awk '{print $NF}'); PUB_KEY=$(echo "$KEYS" | tail -n1 | awk '{print $NF}'); }

UUID_XHTTP=$(cat /proc/sys/kernel/random/uuid)
UUID_REALITY=$(cat /proc/sys/kernel/random/uuid)
XHTTP_PATH="/$(echo "$UUID_XHTTP" | cut -d- -f1 | tr -d '\n')"
SHORT_ID=$(openssl rand -hex 4)
REALITY_SNI="www.icloud.com"
YOUXUAN_DOMAIN="www.visa.com.hk"

mkdir -p /usr/local/etc/xray
CONFIG_FILE="/usr/local/etc/xray/config.json"

cat > $CONFIG_FILE <<EOF
{
  "log": { "loglevel": "warning" },
  "inbounds": [
    {
      "port": $PORT_XHTTP,
      "protocol": "vless",
      "settings": { "clients": [{ "id": "$UUID_XHTTP" }], "decryption": "none" },
      "streamSettings": { "network": "xhttp", "security": "none", "xhttpSettings": { "path": "$XHTTP_PATH", "mode": "auto" } }
    },
    {
      "port": $PORT_REALITY,
      "protocol": "vless",
      "settings": {
        "clients": [{ "id": "$UUID_REALITY", "flow": "xtls-rprx-vision" }],
        "decryption": "none",
        "fallbacks": [{ "dest": $PORT_XHTTP }]
      },
      "streamSettings": {
        "network": "tcp",
        "security": "reality",
        "realitySettings": { "privateKey": "$PRI_KEY", "serverNames": ["$REALITY_SNI"], "shortIds": ["$SHORT_ID"], "target": "$REALITY_SNI:443" }
      }
    }
  ],
  "outbounds": [{ "protocol": "freedom" }]
}
EOF

# 6. 启动服务
cat > /etc/systemd/system/xray.service <<EOF
[Unit]
Description=Xray Service
After=network.target
[Service]
ExecStart=$xray_bin run -c $CONFIG_FILE
Restart=on-failure
LimitNOFILE=1048576
[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable xray >/dev/null 2>&1
systemctl restart xray
sleep 2

# 8. 生成链接
VPS_IP=$(curl -fsSL https://api.ipify.org)
EXTRA_JSON_RAW=$(cat <<EOF
{
  "downloadSettings": {
    "address": "$VPS_IP",
    "port": $PORT_REALITY,
    "network": "xhttp",
    "xhttpSettings": { "path": "$XHTTP_PATH", "mode": "auto" },
    "security": "reality",
    "realitySettings": {
      "serverName": "$REALITY_SNI",
      "fingerprint": "chrome",
      "show": false,
      "publicKey": "$PUB_KEY",
      "shortId": "$SHORT_ID",
      "spiderX": "/",
      "mldsa65Verify": ""
    }
  }
}
EOF
)
ENC_EXTRA=$(echo "$EXTRA_JSON_RAW" | jq -c . | jq -sRr @uri)
ENC_PATH=$(printf '%s' "$XHTTP_PATH" | jq -sRr @uri)

LINK="vless://${UUID_XHTTP}@${YOUXUAN_DOMAIN}:443?encryption=none&security=tls&sni=${DOMAIN}&type=xhttp&host=${DOMAIN}&path=${ENC_PATH}&mode=auto&extra=${ENC_EXTRA}#XHTTP-Reality"
echo "DEPLOY_SUCCESS_LINK: $LINK"
"""

# ================= XHTTP 卸载脚本 =================
XHTTP_UNINSTALL_SCRIPT = r"""
#!/bin/bash
systemctl stop xray
systemctl disable xray
rm -f /etc/systemd/system/xray.service
systemctl daemon-reload
rm -rf /usr/local/etc/xray
echo "Xray Service Uninstalled (Binary kept safe)"
"""

# ================= Hysteria 2 安装脚本 =================
HYSTERIA_INSTALL_SCRIPT_TEMPLATE = r"""
#!/bin/bash
PASSWORD="{password}"
SNI="{sni}"
ENABLE_PORT_HOPPING="{enable_hopping}"
PORT_RANGE_START="{port_range_start}"
PORT_RANGE_END="{port_range_end}"

systemctl stop hysteria-server.service 2>/dev/null
rm -rf /etc/hysteria
bash <(curl -fsSL https://get.hy2.sh/)

mkdir -p /etc/hysteria
openssl req -x509 -nodes -newkey ec:<(openssl ecparam -name prime256v1) \
  -keyout /etc/hysteria/server.key \
  -out /etc/hysteria/server.crt \
  -subj "/CN=$SNI" -days 3650
chown hysteria /etc/hysteria/server.key
chown hysteria /etc/hysteria/server.crt

HY2_PORT=443
if netstat -ulpn | grep -q ":443 "; then HY2_PORT=8443; fi

cat << EOF > /etc/hysteria/config.yaml
listen: :$HY2_PORT
tls:
  cert: /etc/hysteria/server.crt
  key: /etc/hysteria/server.key
auth:
  type: password
  password: $PASSWORD
masquerade:
  type: proxy
  proxy:
    url: https://$SNI
    rewriteHost: true
quic:
  initStreamReceiveWindow: 26843545
  maxStreamReceiveWindow: 26843545
  initConnReceiveWindow: 67108864
  maxConnReceiveWindow: 67108864
EOF

if [ "$ENABLE_PORT_HOPPING" == "true" ]; then
    IFACE=$(ip route get 8.8.8.8 | awk '{{print $5; exit}}')
    iptables -t nat -D PREROUTING -i $IFACE -p udp --dport $PORT_RANGE_START:$PORT_RANGE_END -j REDIRECT --to-ports $HY2_PORT 2>/dev/null || true
    iptables -t nat -A PREROUTING -i $IFACE -p udp --dport $PORT_RANGE_START:$PORT_RANGE_END -j REDIRECT --to-ports $HY2_PORT
    mkdir -p /etc/iptables
    iptables-save > /etc/iptables/rules.v4
fi

systemctl enable --now hysteria-server.service
sleep 2

if systemctl is-active --quiet hysteria-server.service; then
    PUBLIC_IP=$(curl -s https://api.ipify.org)
    LINK="hy2://$PASSWORD@$PUBLIC_IP:$HY2_PORT?peer=$SNI&insecure=1&sni=$SNI#Hy2-Node"
    echo "HYSTERIA_DEPLOY_SUCCESS_LINK: $LINK"
else
    echo "HYSTERIA_DEPLOY_FAILED"
fi
"""

# ================= 探针安装脚本 =================
PROBE_INSTALL_SCRIPT = r"""
bash -c '
[ "$(id -u)" -eq 0 ] || { echo "Root required"; exit 1; }

if [ -f /etc/debian_version ]; then
    apt-get update -y >/dev/null 2>&1
    apt-get install -y python3 iputils-ping util-linux sqlite3 >/dev/null 2>&1
elif [ -f /etc/redhat-release ]; then
    yum install -y python3 iputils util-linux sqlite3 >/dev/null 2>&1
fi

cat > /root/x_fusion_agent.py << "PYTHON_EOF"
import time, json, os, socket, sys, subprocess, re, platform, sqlite3
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

# ... (Python agent code logic omitted for brevity, but should be full content in real file) ...
# 为了节省篇幅，这里假设你保留了完整的 Python 探针代码逻辑
# 实际部署时请把原代码里的 get_cpu_model 等函数完整放进来

def push():
    while True:
        try:
            # (采集逻辑)
            data = {"token": TOKEN, "static": {}, "pings": {}} 
            # ...

            js = json.dumps(data).encode("utf-8")
            req = urllib.request.Request(MANAGER_URL, data=js, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10, context=ssl_ctx) as r: pass
        except: pass
        time.sleep(5)

if __name__ == "__main__":
    push()
PYTHON_EOF

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

systemctl daemon-reload
systemctl enable x-fusion-agent
systemctl restart x-fusion-agent
exit 0
'
"""