# utils.py
import os
import json
import base64
import time
import re
import socket
import logging
import uuid
import io  # 确保导入 io
from urllib.parse import urlparse, quote, parse_qs
import paramiko
import requests
from nicegui import ui  # ✨✨✨ [修复1] 必须导入 ui，否则 notify 会报错

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
    """简单的 IP 转国旗"""
    try:
        # 这里使用 ip-api.com 作为示例，实际生产环境建议加缓存
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
        
        # 获取密钥内容字符串
        key_content = ""
        if auth_type == '独立密钥':
            key_content = server_conf.get('ssh_key')
        elif auth_type == '全局密钥':
            key_content = load_global_key()
        
        # ✨✨✨ [修复2] 增强密钥解析逻辑 (RSA + Ed25519) ✨✨✨
        if key_content:
            key_file = io.StringIO(key_content)
            try:
                # 先尝试 RSA
                pkey = paramiko.RSAKey.from_private_key(key_file)
            except:
                # 失败则尝试 Ed25519
                try:
                    key_file.seek(0)
                    pkey = paramiko.Ed25519Key.from_private_key(key_file)
                except Exception as e:
                    return None, f"无法识别的私钥格式: {e}"

        if auth_type == '独立密码':
            password = server_conf.get('ssh_password')

        # 连接时禁用 agent 和系统配置，防止干扰
        client.connect(host, port, user, pkey=pkey, password=password, timeout=10, banner_timeout=10, look_for_keys=False, allow_agent=False)
        return client, "Success"
    except Exception as e:
        return None, str(e)


def _ssh_exec_wrapper(server_conf, cmd):
    """SSH 执行包装器"""
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

    def get_zone_id(self):
        """改为同步方法，方便在 io_bound 中调用"""
        if not self.token or not self.root_domain: return None
        try:
            r = requests.get(f"{self.base_url}/zones?name={self.root_domain}", headers=self.headers, timeout=10)
            res = r.json()
            if res['success'] and len(res['result']) > 0:
                return res['result'][0]['id']
        except:
            pass
        return None

    def auto_configure(self, ip, sub_domain):
        """自动添加 A 记录并开启 CDN"""
        zone_id = self.get_zone_id()
        if not zone_id: return False, "Zone ID not found"

        data = {"type": "A", "name": sub_domain, "content": ip, "ttl": 1, "proxied": True}
        try:
            r = requests.post(f"{self.base_url}/zones/{zone_id}/dns_records", headers=self.headers, json=data, timeout=10)
            res = r.json()
            if res['success']: return True, "Success"
            if "already exists" in str(res.get('errors')):
                return False, "Record already exists"
            return False, str(res.get('errors'))
        except Exception as e:
            return False, str(e)

    def delete_record_by_domain(self, domain):
        """删除 DNS 记录"""
        zone_id = self.get_zone_id()
        if not zone_id: return False, "Zone ID not found"

        try:
            r = requests.get(f"{self.base_url}/zones/{zone_id}/dns_records?name={domain}", headers=self.headers, timeout=10)
            recs = r.json().get('result', [])
            if not recs: return True, "Record not found (already deleted)"

            rec_id = recs[0]['id']
            r2 = requests.delete(f"{self.base_url}/zones/{zone_id}/dns_records/{rec_id}", headers=self.headers, timeout=10)
            if r2.json().get('success'): return True, "Deleted"
            return False, "Delete failed"
        except Exception as e:
            return False, str(e)


# ================= 节点链接解析与生成 =================
def generate_node_link(node, host_override=None):
    """根据节点数据生成 vless/vmess/hy2 链接"""
    if node.get('_raw_link'): return node['_raw_link']

    proto = node.get('protocol')
    uuid_str = ""

    # 类型检查
    settings = node.get('settings', {})
    if isinstance(settings, str):
        try: settings = json.loads(settings)
        except: settings = {}

    stream = node.get('streamSettings', {})
    if isinstance(stream, str):
        try: stream = json.loads(stream)
        except: stream = {}

    net = stream.get('network', 'tcp')
    security = stream.get('security', 'none')
    port = node.get('port')
    ps = node.get('remark', 'node')
    add = host_override if host_override else "127.0.0.1"

    if proto == 'vless':
        try:
            clients = settings.get('clients', [{}])
            if clients: uuid_str = clients[0].get('id', '')
        except: return ""

        link = f"vless://{uuid_str}@{add}:{port}?security={security}&type={net}"

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
            if isinstance(headers, str):
                try: headers = json.loads(headers)
                except: headers = {}
            host_h = headers.get('Host', '')
            link += f"&path={quote(path)}"
            if host_h: link += f"&host={host_h}"

        link += f"#{quote(ps)}"
        return link

    elif proto == 'vmess':
        try:
            clients = settings.get('clients', [{}])
            if clients: uuid_str = clients[0].get('id', '')
        except: return ""
        
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
                try: headers = json.loads(headers)
                except: headers = {}
            v_json['host'] = headers.get('Host', '')

        return "vmess://" + safe_base64(json.dumps(v_json))

    return ""


def parse_vless_link_to_node(link, remark_override=None):
    """简单解析 VLESS/Hy2 链接"""
    try:
        parsed = urlparse(link)
        node = {
            "id": str(uuid.uuid4()),
            "remark": remark_override if remark_override else (parsed.fragment or "Imported"),
            "port": parsed.port or 443,
            "protocol": parsed.scheme,
            "settings": {},
            "streamSettings": {},
            "enable": True,
            "_is_custom": True,
            "_raw_link": link
        }
        return node
    except:
        return None


def generate_detail_config(node, host):
    """生成 Surge/Clash 样式的明文配置行"""
    if node.get('_raw_link'): return f"// Custom Node: {node.get('remark')} \n// Link: {node['_raw_link']}"
    return f"// {node.get('remark')}: Auto-gen not supported"


# ================= 管理器适配器 (Adapter) =================

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
        except: pass
        return False

    def get_inbounds(self):
        if not self.cookie and not self.login(): return []
        try:
            r = requests.post(f"{self.url}/xui/inbound/list", cookies=self.cookie, timeout=5)
            res = r.json()
            if res.get('success'): return res.get('obj', [])
        except: pass
        return []

    def add_inbound(self, data):
        if not self.cookie and not self.login(): return False, "Login failed"
        try:
            r = requests.post(f"{self.url}/xui/inbound/add", cookies=self.cookie, data=data, timeout=5)
            return r.json().get('success'), r.json().get('msg')
        except Exception as e: return False, str(e)

    def update_inbound(self, id, data):
        if not self.cookie and not self.login(): return False, "Login failed"
        try:
            r = requests.post(f"{self.url}/xui/inbound/update/{id}", cookies=self.cookie, data=data, timeout=5)
            return r.json().get('success'), r.json().get('msg')
        except Exception as e: return False, str(e)

    def delete_inbound(self, id):
        if not self.cookie and not self.login(): return False, "Login failed"
        try:
            r = requests.post(f"{self.url}/xui/inbound/del/{id}", cookies=self.cookie, timeout=5)
            return r.json().get('success'), r.json().get('msg')
        except Exception as e: return False, str(e)


class XUI_SSH_Manager:
    """通过 SSH 直接操作 SQLite 数据库"""
    def __init__(self, server_conf):
        self.conf = server_conf
        self.db_path = "/etc/x-ui/x-ui.db"

    def _to_hex(self, s):
        if isinstance(s, dict) or isinstance(s, list): s = json.dumps(s, ensure_ascii=False)
        return str(s).encode('utf-8').hex()

    def get_inbounds(self):
        sql = "SELECT id, up, down, total, remark, enable, protocol, port, settings, stream_settings FROM inbounds;"
        cmd = f"sqlite3 {self.db_path} '{sql}' -json"
        success, output = _ssh_exec_wrapper(self.conf, cmd)
        if success and output.strip():
            try: return json.loads(output)
            except: pass
        return []

    def add_inbound(self, data):
        try:
            remark = self._to_hex(data.get('remark', ''))
            protocol = data.get('protocol', '')
            port = int(data.get('port', 0))
            settings = self._to_hex(data.get('settings', {}))
            stream_settings = self._to_hex(data.get('streamSettings', {}))
            sniffing = self._to_hex(data.get('sniffing', {}))
            enable = 1 if data.get('enable', True) else 0

            sql = f"INSERT INTO inbounds (remark, port, protocol, settings, stream_settings, sniffing, enable, up, down, total, expiry_time) VALUES (x'{remark}', {port}, '{protocol}', x'{settings}', x'{stream_settings}', x'{sniffing}', {enable}, 0, 0, 0, 0);"
            cmd = f"sqlite3 {self.db_path} \"{sql}\""
            
            success, output = _ssh_exec_wrapper(self.conf, cmd)
            if success:
                _ssh_exec_wrapper(self.conf, "systemctl restart x-ui")
                return True, "Added & Restarted"
            return False, f"DB Error: {output}"
        except Exception as e: return False, str(e)

    def update_inbound(self, id, data):
        try:
            set_parts = []
            if 'remark' in data: set_parts.append(f"remark=x'{self._to_hex(data['remark'])}'")
            if 'port' in data: set_parts.append(f"port={int(data['port'])}")
            if 'protocol' in data: set_parts.append(f"protocol='{data['protocol']}'")
            if 'settings' in data: set_parts.append(f"settings=x'{self._to_hex(data['settings'])}'")
            if 'streamSettings' in data: set_parts.append(f"stream_settings=x'{self._to_hex(data['streamSettings'])}'")
            if 'enable' in data: set_parts.append(f"enable={1 if data['enable'] else 0}")

            if not set_parts: return True, "Nothing to update"

            sql = f"UPDATE inbounds SET {', '.join(set_parts)} WHERE id={id};"
            cmd = f"sqlite3 {self.db_path} \"{sql}\""
            
            success, output = _ssh_exec_wrapper(self.conf, cmd)
            if success:
                _ssh_exec_wrapper(self.conf, "systemctl restart x-ui")
                return True, "Updated & Restarted"
            return False, f"DB Error: {output}"
        except Exception as e: return False, str(e)

    def delete_inbound(self, id):
        try:
            sql = f"DELETE FROM inbounds WHERE id={id};"
            cmd = f"sqlite3 {self.db_path} \"{sql}\""
            success, output = _ssh_exec_wrapper(self.conf, cmd)
            if success:
                _ssh_exec_wrapper(self.conf, "systemctl restart x-ui")
                return True, "Deleted & Restarted"
            return False, f"DB Error: {output}"
        except Exception as e: return False, str(e)

# ================= 消息提示辅助 =================
def safe_notify(msg, type='info'):
    """为了兼容 backend 线程调用，需要判断上下文"""
    try:
        ui.notify(msg, type=type)
    except:
        # 如果不在 UI 上下文中（如后台定时任务），则打印日志
        print(f"[{type.upper()}] {msg}")
