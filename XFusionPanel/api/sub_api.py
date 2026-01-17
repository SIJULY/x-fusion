# api/sub_api.py
import base64
import json
import requests
import asyncio
from urllib.parse import urlparse, quote
from fastapi import APIRouter, Request, Response
from nicegui import run

from core.state import SERVERS_CACHE, SUBS_CACHE, NODES_DATA, ADMIN_CONFIG

sub_router = APIRouter()


# --- 辅助函数 ---
def safe_base64(s):
    return base64.urlsafe_b64encode(s.encode('utf-8')).decode('utf-8')


def decode_base64_safe(s):
    try:
        missing_padding = len(s) % 4
        if missing_padding: s += '=' * (4 - missing_padding)
        return base64.urlsafe_b64decode(s).decode('utf-8')
    except:
        return ""


def generate_node_link(node, server_host):
    try:
        clean_host = server_host
        if '://' in clean_host: clean_host = clean_host.split('://')[-1]
        if ':' in clean_host and not clean_host.startswith('['): clean_host = clean_host.split(':')[0]

        p = node['protocol'];
        remark = node['remark'];
        port = node['port']
        add = node.get('listen') or clean_host

        s = node['settings'] if isinstance(node['settings'], dict) else json.loads(node['settings'])
        st = node['streamSettings'] if isinstance(node['streamSettings'], dict) else json.loads(node['streamSettings'])
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


def generate_detail_config(node, server_host):
    # 用于 Surge 等的配置生成 (略简版)
    try:
        clean_host = server_host.replace('http://', '').replace('https://', '').split(':')[0]
        remark = node.get('remark', 'Unnamed').replace(',', '_')
        port = node['port']

        if node.get('_is_custom'):
            raw = node.get('_raw_link', '')
            if raw.startswith('hy2://'): return f"{remark} = hysteria2, {clean_host}, {port}..."
            return f"// Custom: {remark}"

        # 标准节点处理逻辑... (此处简化，完整版可参考原代码 generate_detail_config)
        return f"// Standard: {remark}"
    except:
        return ""


# --- 核心订阅接口 ---
@sub_router.get('/sub/{token}')
async def sub_handler(token: str, request: Request):
    sub = next((s for s in SUBS_CACHE if s['token'] == token), None)
    if not sub: return Response("Invalid Token", 404)

    links = []
    node_lookup = {}

    for srv in SERVERS_CACHE:
        # 获取 Host
        raw_url = srv['url']
        try:
            if '://' not in raw_url: raw_url = f'http://{raw_url}'
            parsed = urlparse(raw_url)
            host = parsed.hostname or raw_url.split('://')[-1].split(':')[0]
        except:
            host = raw_url

        panel_nodes = NODES_DATA.get(srv['url'], []) or []
        custom_nodes = srv.get('custom_nodes', []) or []

        for n in (panel_nodes + custom_nodes):
            key = f"{srv['url']}|{n['id']}"
            node_lookup[key] = (n, host)

    ordered_ids = sub.get('nodes', [])
    for key in ordered_ids:
        if key in node_lookup:
            node, host = node_lookup[key]
            if node.get('_raw_link'):
                links.append(node['_raw_link'])
            else:
                l = generate_node_link(node, host)
                if l: links.append(l)

    return Response(safe_base64("\n".join(links)), media_type="text/plain; charset=utf-8")


# --- 分组订阅接口 ---
@sub_router.get('/sub/group/{group_b64}')
async def group_sub_handler(group_b64: str):
    group_name = decode_base64_safe(group_b64)
    if not group_name: return Response("Invalid Group", 400)

    links = []
    target_servers = [s for s in SERVERS_CACHE if s.get('group') == group_name or group_name in s.get('tags', [])]

    for srv in target_servers:
        host = srv['url'].split('://')[-1].split(':')[0]
        nodes = (NODES_DATA.get(srv['url'], []) or []) + srv.get('custom_nodes', [])
        for n in nodes:
            if n.get('enable'):
                if n.get('_raw_link'):
                    links.append(n['_raw_link'])
                else:
                    l = generate_node_link(n, host)
                    if l: links.append(l)

    return Response(safe_base64("\n".join(links)), media_type="text/plain; charset=utf-8")


# --- 短链接转换接口 (SubConverter) ---
@sub_router.get('/get/sub/{target}/{token}')
async def short_sub_handler(target: str, token: str, request: Request):
    sub = next((s for s in SUBS_CACHE if s['token'] == token), None)
    if not sub: return Response("Not Found", 404)

    # 构造自身 URL
    custom_base = ADMIN_CONFIG.get('manager_base_url', '').strip().rstrip('/')
    base_url = custom_base if custom_base else str(request.base_url).rstrip('/')
    internal_api = f"{base_url}/sub/{token}"

    # 调用 SubConverter
    params = {
        "target": target, "url": internal_api, "insert": "false",
        "list": "true", "ver": "4", "udp": "true", "scv": "true", "sort": "false"
    }
    converter_api = "http://subconverter:25500/sub"  # 假设你有这个服务，或者使用公网的

    # 如果没有本地 subconverter，直接返回原文 (Surge 也可以直接用 API 模式)
    if target == 'surge':
        # 这里应该调用 generate_detail_config 循环生成
        # 简单处理：直接重定向到原始订阅（Surge 支持 managed-config）
        return Response("请配置本地 SubConverter", 501)

    try:
        def _fetch():
            return requests.get(converter_api, params=params, timeout=10)

        resp = await run.io_bound(_fetch)
        return Response(content=resp.content, media_type="text/plain; charset=utf-8")
    except:
        return Response("SubConverter Error", 502)