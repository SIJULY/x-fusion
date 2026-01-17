import json
import urllib.parse
from urllib.parse import quote
from app.utils.common import safe_base64


def parse_vless_link_to_node(link, remark_override=None):
    """解析 VLESS 链接为 Panel 节点对象"""
    try:
        if not link.startswith("vless://"): return None
        main_part = link.replace("vless://", "")

        remark = "Imported"
        if "#" in main_part:
            main_part, remark = main_part.split("#", 1)
            remark = urllib.parse.unquote(remark)
        if remark_override: remark = remark_override

        params = {}
        if "?" in main_part:
            main_part, query_str = main_part.split("?", 1)
            params = dict(urllib.parse.parse_qsl(query_str))

        if "@" in main_part:
            uuid, host_port = main_part.split("@", 1)
        else:
            return None

        if ":" in host_port:
            host, port = host_port.rsplit(":", 1)
        else:
            host = host_port; port = 443

        final_link = link.split("#")[0] + f"#{urllib.parse.quote(remark)}"

        return {
            "id": uuid, "remark": remark, "port": int(port), "protocol": "vless",
            "settings": {"clients": [{"id": uuid, "flow": params.get("flow", "")}], "decryption": "none"},
            "streamSettings": {
                "network": params.get("type", "tcp"), "security": params.get("security", "none"),
                "xhttpSettings": {"path": params.get("path", ""), "mode": params.get("mode", "auto"),
                                  "host": params.get("host", "")},
                "realitySettings": {"serverName": params.get("sni", ""), "shortId": params.get("sid", ""),
                                    "publicKey": params.get("pbk", "")}
            },
            "enable": True, "_is_custom": True, "_raw_link": final_link
        }
    except:
        return None


def generate_node_link(node, server_host):
    """生成节点分享链接 (VMess/VLESS/Trojan/SS)"""
    try:
        clean_host = server_host.split('://')[-1].split(':')[0]
        p = node['protocol'];
        remark = node['remark'];
        port = node['port']
        add = node.get('listen') or clean_host

        s = json.loads(node['settings']) if isinstance(node['settings'], str) else node['settings']
        st = json.loads(node['streamSettings']) if isinstance(node['streamSettings'], str) else node['streamSettings']
        net = st.get('network', 'tcp');
        tls = st.get('security', 'none');
        path = "";
        host = ""

        if net == 'ws':
            path = st.get('wsSettings', {}).get('path', '/')
            host = st.get('wsSettings', {}).get('headers', {}).get('Host', '')
        elif net == 'grpc':
            path = st.get('grpcSettings', {}).get('serviceName', '')

        if p == 'vmess':
            v = {
                "v": "2", "ps": remark, "add": add, "port": port, "id": s['clients'][0]['id'],
                "aid": "0", "scy": "auto", "net": net, "type": "none", "host": host, "path": path, "tls": tls
            }
            return "vmess://" + safe_base64(json.dumps(v))
        elif p == 'vless':
            params = f"type={net}&security={tls}"
            if path: params += f"&path={path}" if net != 'grpc' else f"&serviceName={path}"
            if host: params += f"&host={host}"
            return f"vless://{s['clients'][0]['id']}@{add}:{port}?{params}#{remark}"
        elif p == 'trojan':
            return f"trojan://{s['clients'][0]['password']}@{add}:{port}?type={net}&security={tls}#{remark}"
        elif p == 'shadowsocks':
            cred = f"{s['method']}:{s['password']}"
            return f"ss://{safe_base64(cred)}@{add}:{port}#{remark}"
    except:
        return ""
    return ""


def generate_converted_link(raw_link, target, domain_prefix):
    """生成 SubConverter 转换链接"""
    if not raw_link or not domain_prefix: return ""
    converter_base = f"{domain_prefix}/convert"
    encoded_url = quote(raw_link)
    params = f"target={target}&url={encoded_url}&insert=false&list=true&ver=4&udp=true&scv=true"
    return f"{converter_base}?{params}"