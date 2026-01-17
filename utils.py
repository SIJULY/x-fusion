# utils.py
import os
import json
import base64
import time
import re
import socket
import logging
import uuid
from urllib.parse import urlparse, quote, parse_qs
import paramiko
import requests

import config
import state

logger = logging.getLogger("XUI_Utils")


# ================= 基础工具 =================
def format_bytes(size):
    power = 2 ** 10
    n = 0
    power_labels = {0: '', 1: 'K', 2: 'M', 3: 'G', 4: 'T'}
    while size > power:
        size /= power
        n += 1
    return f"{size:.2f} {power_labels[n]}B"


def safe_base64(s):
    """生成 URL 安全的 Base64"""
    if not isinstance(s, bytes): s = s.encode('utf-8')
    return base64.urlsafe_b64encode(s).decode('utf-8').replace('=', '')


def decode_base64_safe(s):
    """解码 Base64 (兼容 URL Safe 和普通)"""
    s = s.strip()
    missing_padding = len(s) % 4
    if missing_padding: s += '=' * (4 - missing_padding)
    try:
        return base64.urlsafe_b64decode(s).decode('utf-8')
    except:
        try:
            return base64.b64decode(s).decode('utf-8')
        except:
            return ""


def get_flag_from_ip(ip):
    """简单的 IP 转国旗 (使用纯真库或API，这里用简单逻辑或 requests)"""
    # 示例：实际项目中可以接入 ip-api.com
    try:
        resp = requests.get(f"http://ip-api.com/json/{ip}?fields=countryCode", timeout=3)
        if resp.status_code == 200:
            cc = resp.json().get('countryCode')
            return get_flag_for_country(cc)
    except:
        pass
    return "🏳️"


def get_flag_for_country(cc):
    if not cc: return "🏳️"
    # 将 ISO 3166-1 代码转换为 Unicode 国旗
    return chr(ord(cc[0]) + 127397) + chr(ord(cc[1]) + 127397)


def get_coords_from_name(name):
    """从名字中猜测坐标 (利用 config.LOCATION_COORDS)"""
    for k, v in config.LOCATION_COORDS.items():
        if k in name: return v
    return None


# ================= SSH 相关工具 =================
def load_global_key():
    if os.path.exists(config.GLOBAL_SSH_KEY_FILE):
        with open(config.GLOBAL_SSH_KEY_FILE, 'r') as f:
            return f.read().strip()
    return ""


def save_global_key(key_content):
    with open(config.GLOBAL_SSH_KEY_FILE, 'w') as f:
        f.write(key_content.strip())


def get_ssh_client_sync(server_conf):
    """同步获取 SSH 客户端 (用于 run.io_bound)"""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    host = server_conf.get('ssh_host') or \
           server_conf.get('url', '').replace('http://', '').replace('https://', '').split(':')[0]
    port = int(server_conf.get('ssh_port', 22))
    user = server_conf.get('ssh_user', 'root')

    try:
        auth_type = server_conf.get('ssh_auth_type', '全局密钥')

        pkey = None
        password = None

        if auth_type == '独立密码':
            password = server_conf.get('ssh_password')
        elif auth_type == '独立密钥':
            key_str = server_conf.get('ssh_key')
            if key_str: pkey = paramiko.RSAKey.from_private_key(io.StringIO(key_str))
        else:  # 全局密钥
            global_key = load_global_key()
            if global_key: pkey = paramiko.RSAKey.from_private_key(io.StringIO(global_key))

        client.connect(host, port, user, pkey=pkey, password=password, timeout=10, banner_timeout=10)
        return client, "Success"
    except Exception as e:
        return None, str(e)


def _ssh_exec_wrapper(server_conf, cmd):
    """SSH 执行包装器"""
    import io
    client, msg = get_ssh_client_sync(server_conf)
    if not client: return False, f"Connect Error: {msg}"

    try:
        stdin, stdout, stderr = client.exec_command(cmd, timeout=30)
        out = stdout.read().decode().strip()
        err = stderr.read().decode().strip()
        client.close()
        return True, (out + "\n" + err).strip()
    except Exception as e:
        return False, str(e)


# ================= Cloudflare API =================
class CloudflareHandler:
    def __init__(self):
        self.token = state.ADMIN_CONFIG.get('cf_api_token')
        self.root_domain = state.ADMIN_CONFIG.get('cf_root_domain')
        self.headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
        self.base_url = "https://api.cloudflare.com/client/v4"

    async def get_zone_id(self):
        if not self.token or not self.root_domain: return None
        try:
            r = requests.get(f"{self.base_url}/zones?name={self.root_domain}", headers=self.headers)
            res = r.json()
            if res['success'] and len(res['result']) > 0:
                return res['result'][0]['id']
        except:
            pass
        return None

    async def auto_configure(self, ip, sub_domain):
        """自动添加 A 记录并开启 CDN"""
        zone_id = await self.get_zone_id()
        if not zone_id: return False, "Zone ID not found"

        data = {"type": "A", "name": sub_domain, "content": ip, "ttl": 1, "proxied": True}
        try:
            r = requests.post(f"{self.base_url}/zones/{zone_id}/dns_records", headers=self.headers, json=data)
            res = r.json()
            if res['success']: return True, "Success"
            # 如果已存在，尝试更新
            if "already exists" in str(res.get('errors')):
                # 需先查询 ID 再 PUT，这里简化处理，认为失败
                return False, "Record already exists"
            return False, str(res.get('errors'))
        except Exception as e:
            return False, str(e)

    async def delete_record_by_domain(self, domain):
        """删除 DNS 记录"""
        zone_id = await self.get_zone_id()
        if not zone_id: return False, "Zone ID not found"

        try:
            # 1. 查找记录
            r = requests.get(f"{self.base_url}/zones/{zone_id}/dns_records?name={domain}", headers=self.headers)
            recs = r.json().get('result', [])
            if not recs: return True, "Record not found (already deleted)"

            rec_id = recs[0]['id']
            # 2. 删除
            r2 = requests.delete(f"{self.base_url}/zones/{zone_id}/dns_records/{rec_id}", headers=self.headers)
            if r2.json().get('success'): return True, "Deleted"
            return False, "Delete failed"
        except Exception as e:
            return False, str(e)


# ================= 节点链接解析与生成 =================
# [utils.py] 请替换原有的 generate_node_link 函数
def generate_node_link(node, host_override=None):
    """根据节点数据生成 vless/vmess/hy2 链接 (增强容错版)"""
    # 优先使用 raw_link
    if node.get('_raw_link'): return node['_raw_link']

    proto = node.get('protocol')
    uuid_str = ""

    # --- 修复核心：类型检查与转换 ---
    settings = node.get('settings', {})
    if isinstance(settings, str):
        try:
            settings = json.loads(settings)
        except:
            settings = {}

    stream = node.get('streamSettings', {})
    if isinstance(stream, str):
        try:
            stream = json.loads(stream)
        except:
            stream = {}
    # -----------------------------

    net = stream.get('network', 'tcp')
    security = stream.get('security', 'none')
    port = node.get('port')
    ps = node.get('remark', 'node')

    if host_override:
        add = host_override
    else:
        add = "127.0.0.1"  # 默认回退

    if proto == 'vless':
        try:
            clients = settings.get('clients', [{}])
            if clients: uuid_str = clients[0].get('id', '')
        except:
            return ""

        link = f"vless://{uuid_str}@{add}:{port}?security={security}&type={net}&headerType=none"

        # 处理 Reality / TLS / WS
        if security == 'reality':
            r_set = stream.get('realitySettings', {})
            pbk = r_set.get('publicKey', '')
            sni = r_set.get('serverNames', [''])[0] if r_set.get('serverNames') else ''
            link += f"&sni={sni}&pbk={pbk}&fp=chrome"
        elif security == 'tls':
            tls = stream.get('tlsSettings', {})
            sni = tls.get('serverName', '')
            link += f"&sni={sni}"

        if net == 'ws':
            ws = stream.get('wsSettings', {})
            path = ws.get('path', '/')
            headers = ws.get('headers', {})
            # 兼容 headers 可能是字符串的情况
            if isinstance(headers, str):
                try:
                    headers = json.loads(headers)
                except:
                    headers = {}
            host_h = headers.get('Host', '')
            link += f"&path={quote(path)}"
            if host_h: link += f"&host={host_h}"

        link += f"#{quote(ps)}"
        return link

    elif proto == 'vmess':
        try:
            clients = settings.get('clients', [{}])
            if clients: uuid_str = clients[0].get('id', '')
        except:
            return ""
        # 构造 VMess JSON
        v_json = {
            "v": "2", "ps": ps, "add": add, "port": port, "id": uuid_str, "aid": "0",
            "net": net, "type": "none", "host": "", "path": "", "tls": ""
        }
        if security == 'tls': v_json['tls'] = 'tls'

        if net == 'ws':
            ws = stream.get('wsSettings', {})
            v_json['path'] = ws.get('path', '/')
            headers = ws.get('headers', {})
            if isinstance(headers, str):
                try:
                    headers = json.loads(headers)
                except:
                    headers = {}
            v_json['host'] = headers.get('Host', '')

        return "vmess://" + base64.b64encode(json.dumps(v_json).encode()).decode()

    return ""


def parse_vless_link_to_node(link, remark_override=None):
    """解析 VLESS/Hy2 链接为内部节点格式 (仅支持部分)"""
    try:
        parsed = urlparse(link)
        proto = parsed.scheme
        user_info = parsed.username
        host = parsed.hostname
        port = parsed.port
        params = parse_qs(parsed.query)
        hash_tag = parsed.fragment

        node = {
            "id": str(uuid.uuid4()),
            "remark": remark_override if remark_override else (hash_tag or "Imported"),
            "port": port,
            "protocol": proto,
            "settings": {},
            "streamSettings": {},
            "enable": True,
            "_is_custom": True,
            "_raw_link": link
        }
        # 简单存储，不做深度解析，因为 _raw_link 已经保存了
        return node
    except:
        return None


def generate_detail_config(node, host):
    """生成 Surge/Clash 样式的明文配置行"""
    # 优先使用 raw
    if node.get('_raw_link'): return f"// Custom Node: {node.get('remark')} \n// Link: {node['_raw_link']}"

    # 这里只演示 VLESS Reality 的生成
    proto = node.get('protocol')
    if proto == 'vless':
        s = node.get('streamSettings', {})
        if s.get('security') == 'reality':
            # Surge 格式
            uuid = node['settings']['clients'][0]['id']
            r = s['realitySettings']
            sni = r.get('serverNames', [''])[0]
            pbk = r.get('publicKey')
            fp = r.get('fingerprint', 'chrome')
            return f"{node['remark']} = vless, {host}, {node['port']}, username={uuid}, tls=true, tfo=true, mptcp=true, tls-verification=true, tls-server-name={sni}, tls-public-key={pbk}, tls-fingerprint={fp}, ip-version=ipv4-preferred"

    return f"// {node.get('remark')}: Config generation not fully supported for {proto}"


# ================= 管理器适配器 (Adapter) =================
# 用于统一 SSH 和 API 的调用接口

class XUI_API_Manager:
    def __init__(self, server_conf):
        self.url = server_conf['url'].rstrip('/')
        self.user = server_conf['user']
        self.pwd = server_conf['pass']
        self.cookie = None

    def login(self):
        try:
            r = requests.post(f"{self.url}/login", data={"username": self.user, "password": self.pwd}, timeout=5)
            if r.status_code == 200 and r.json().get('success'):
                self.cookie = r.cookies
                return True
        except:
            pass
        return False

    def get_inbounds(self):
        if not self.cookie and not self.login(): return []
        try:
            r = requests.post(f"{self.url}/xui/inbound/list", cookies=self.cookie, timeout=5)
            res = r.json()
            if res.get('success'):
                return res.get('obj', [])
        except:
            pass
        return []

    def add_inbound(self, data):
        if not self.cookie and not self.login(): return False, "Login failed"
        try:
            r = requests.post(f"{self.url}/xui/inbound/add", cookies=self.cookie, data=data, timeout=5)
            return r.json().get('success'), r.json().get('msg')
        except Exception as e:
            return False, str(e)

    def update_inbound(self, id, data):
        if not self.cookie and not self.login(): return False, "Login failed"
        try:
            r = requests.post(f"{self.url}/xui/inbound/update/{id}", cookies=self.cookie, data=data, timeout=5)
            return r.json().get('success'), r.json().get('msg')
        except Exception as e:
            return False, str(e)

    def delete_inbound(self, id):
        if not self.cookie and not self.login(): return False, "Login failed"
        try:
            r = requests.post(f"{self.url}/xui/inbound/del/{id}", cookies=self.cookie, timeout=5)
            return r.json().get('success'), r.json().get('msg')
        except Exception as e:
            return False, str(e)


class XUI_SSH_Manager:
    """
    通过 SSH 直接操作 SQLite 数据库 (高级功能 - 完整实现版)
    注意：此操作会修改数据库并重启 x-ui 面板，请谨慎使用。
    """

    def __init__(self, server_conf):
        self.conf = server_conf
        self.db_path = "/etc/x-ui/x-ui.db"

    def _to_hex(self, s):
        """辅助函数：将字符串或字典转为十六进制，防止 Shell 引号转义灾难"""
        if isinstance(s, dict) or isinstance(s, list):
            s = json.dumps(s, ensure_ascii=False)
        return str(s).encode('utf-8').hex()

    def get_inbounds(self):
        """获取所有入站节点"""
        # 构造 SQL 查询，-json 参数让 sqlite3 直接输出 JSON 格式
        sql = "SELECT id, up, down, total, remark, enable, protocol, port, settings, stream_settings FROM inbounds;"
        cmd = f"sqlite3 {self.db_path} '{sql}' -json"

        # 调用同步的 ssh wrapper
        success, output = _ssh_exec_wrapper(self.conf, cmd)

        if success and output.strip():
            try:
                return json.loads(output)
            except:
                # 某些老版本 sqlite3 可能不支持 -json，或者输出为空
                pass
        return []

    def add_inbound(self, data):
        """添加节点 (INSERT)"""
        try:
            # 1. 准备字段并转 Hex
            remark = self._to_hex(data.get('remark', ''))
            protocol = data.get('protocol', '')
            port = int(data.get('port', 0))
            settings = self._to_hex(data.get('settings', {}))
            stream_settings = self._to_hex(data.get('streamSettings', {}))
            sniffing = self._to_hex(data.get('sniffing', {}))
            enable = 1 if data.get('enable', True) else 0

            # 2. 构造 SQL (利用 SQLite x'...' 语法读取 hex 数据)
            # 默认流量 up/down/total 设为 0
            sql = f"INSERT INTO inbounds (remark, port, protocol, settings, stream_settings, sniffing, enable, up, down, total, expiry_time) VALUES (x'{remark}', {port}, '{protocol}', x'{settings}', x'{stream_settings}', x'{sniffing}', {enable}, 0, 0, 0, 0);"

            # 3. 执行 SQL
            cmd = f"sqlite3 {self.db_path} \"{sql}\""
            success, output = _ssh_exec_wrapper(self.conf, cmd)

            if success:
                # 4. 重启面板以加载新配置 (必须步骤)
                _ssh_exec_wrapper(self.conf, "systemctl restart x-ui")
                return True, "Added & Restarted"
            return False, f"DB Error: {output}"
        except Exception as e:
            return False, str(e)

    def update_inbound(self, id, data):
        """更新节点 (UPDATE)"""
        try:
            # 1. 动态构造 SET 子句
            set_parts = []
            if 'remark' in data:
                set_parts.append(f"remark=x'{self._to_hex(data['remark'])}'")
            if 'port' in data:
                set_parts.append(f"port={int(data['port'])}")
            if 'protocol' in data:
                set_parts.append(f"protocol='{data['protocol']}'")
            if 'settings' in data:
                set_parts.append(f"settings=x'{self._to_hex(data['settings'])}'")
            if 'streamSettings' in data:
                set_parts.append(f"stream_settings=x'{self._to_hex(data['streamSettings'])}'")
            if 'sniffing' in data:
                set_parts.append(f"sniffing=x'{self._to_hex(data['sniffing'])}'")
            if 'enable' in data:
                set_parts.append(f"enable={1 if data['enable'] else 0}")

            if not set_parts:
                return True, "Nothing to update"

            # 2. 执行更新
            sql = f"UPDATE inbounds SET {', '.join(set_parts)} WHERE id={id};"
            cmd = f"sqlite3 {self.db_path} \"{sql}\""

            success, output = _ssh_exec_wrapper(self.conf, cmd)
            if success:
                _ssh_exec_wrapper(self.conf, "systemctl restart x-ui")
                return True, "Updated & Restarted"
            return False, f"DB Error: {output}"
        except Exception as e:
            return False, str(e)

    def delete_inbound(self, id):
        """删除节点 (DELETE)"""
        try:
            sql = f"DELETE FROM inbounds WHERE id={id};"
            cmd = f"sqlite3 {self.db_path} \"{sql}\""

            success, output = _ssh_exec_wrapper(self.conf, cmd)
            if success:
                _ssh_exec_wrapper(self.conf, "systemctl restart x-ui")
                return True, "Deleted & Restarted"
            return False, f"DB Error: {output}"
        except Exception as e:
            return False, str(e)

# ================= 消息提示辅助 =================
def safe_notify(msg, type='info'):
    """为了兼容 backend 线程调用，需要判断上下文"""
    try:
        ui.notify(msg, type=type)
    except:
        print(f"[{type.upper()}] {msg}")