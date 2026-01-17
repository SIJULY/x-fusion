# ui/dialogs/deploy.py
import uuid
import random
import string
import urllib.parse
import re
from nicegui import ui, run

from core.state import SERVERS_CACHE
from core.storage import save_servers
from services.cloudflare import CloudflareHandler
from services.ssh_manager import _ssh_exec_wrapper
from services.install_scripts import XHTTP_INSTALL_SCRIPT_TEMPLATE, HYSTERIA_INSTALL_SCRIPT_TEMPLATE
from ui.common import safe_notify


# ================= 辅助：解析 VLESS 链接 =================
def parse_vless_link_to_node(link, remark_override=None):
    try:
        if not link.startswith("vless://"): return None
        # 基础解析
        main_part = link.replace("vless://", "")
        remark = "XHTTP-Reality"
        if "#" in main_part:
            main_part, remark = main_part.split("#", 1)
            remark = urllib.parse.unquote(remark)

        if remark_override: remark = remark_override

        params = {}
        if "?" in main_part:
            main_part, query_str = main_part.split("?", 1)
            params = dict(urllib.parse.parse_qsl(query_str))

        if "@" in main_part:
            user_info, host_port = main_part.split("@", 1)
            uuid_val = user_info
        else:
            return None

        if ":" in host_port:
            host, port = host_port.rsplit(":", 1)
        else:
            host = host_port; port = 443

        # 重新构建链接以确保存储标准
        final_link = f"vless://{uuid_val}@{host}:{port}?{query_str}#{urllib.parse.quote(remark)}"

        return {
            "id": uuid_val, "remark": remark, "port": int(port), "protocol": "vless",
            "settings": {"clients": [{"id": uuid_val, "flow": params.get("flow", "")}], "decryption": "none"},
            "streamSettings": {
                "network": params.get("type", "tcp"),
                "security": params.get("security", "none"),
                "xhttpSettings": {"path": params.get("path", ""), "mode": params.get("mode", "auto"),
                                  "host": params.get("host", "")},
                "realitySettings": {"serverName": params.get("sni", ""), "shortId": params.get("sid", ""),
                                    "publicKey": params.get("pbk", "")}
            },
            "enable": True, "_is_custom": True, "_raw_link": final_link
        }
    except:
        return None


# ================= 部署 XHTTP =================
async def open_deploy_xhttp_dialog(server_conf, callback):
    # 获取 IP
    target_host = server_conf.get('ssh_host') or \
                  server_conf.get('url', '').replace('http://', '').replace('https://', '').split(':')[0]
    import socket
    if not re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", target_host):
        try:
            target_host = await run.io_bound(socket.gethostbyname, target_host)
        except:
            safe_notify(f"❌ 无法解析 IP: {target_host}", "negative"); return

    # 检查 CF
    cf_handler = CloudflareHandler()
    if not cf_handler.token or not cf_handler.root_domain:
        safe_notify("❌ 请先在设置中配置 Cloudflare API", "negative");
        return

    # 生成域名
    rand_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
    sub_prefix = f"node-{target_host.replace('.', '-')}-{rand_suffix}"
    target_domain = f"{sub_prefix}.{cf_handler.root_domain}"

    with ui.dialog() as d, ui.card().classes('w-[500px] p-0 gap-0 overflow-hidden rounded-xl shadow-2xl'):
        # 顶部深色 Header
        with ui.column().classes('w-full bg-slate-900 p-6 gap-2'):
            with ui.row().classes('items-center gap-2 text-white'):
                ui.icon('rocket_launch', size='md')
                ui.label('部署 XHTTP-Reality (V76)').classes('text-lg font-bold')
            ui.label(f"目标域名: {target_domain}").classes('text-xs text-green-400 font-mono')

        with ui.column().classes('w-full p-6 gap-4'):
            ui.label('节点备注名称').classes('text-xs font-bold text-gray-500 mb-[-8px]')
            remark_input = ui.input(placeholder=f'Reality-{target_domain}').props('outlined dense clearable').classes(
                'w-full')

            # 日志区
            log_area = ui.log().classes(
                'w-full h-48 bg-gray-900 text-green-400 text-[11px] font-mono p-3 rounded border border-gray-700 hidden transition-all')

        with ui.row().classes('w-full p-4 bg-gray-50 border-t border-gray-200 justify-end gap-3'):
            btn_cancel = ui.button('取消', on_click=d.close).props('flat color=grey')

            async def run_deploy():
                btn_cancel.disable();
                btn_deploy.props('loading');
                log_area.classes(remove='hidden')
                try:
                    log_area.push(f"🔄 [Cloudflare] 解析域名: {target_domain} -> {target_host}")
                    ok, msg = await cf_handler.auto_configure(target_host, sub_prefix)
                    if not ok: raise Exception(f"CF 配置失败: {msg}")

                    log_area.push(f"🚀 [SSH] 下发安装脚本...")
                    # 注入脚本
                    cmd = f"cat > /tmp/install_xhttp.sh << 'EOF_SCRIPT'\n{XHTTP_INSTALL_SCRIPT_TEMPLATE}\nEOF_SCRIPT\nbash /tmp/install_xhttp.sh \"{target_domain}\""

                    success, output = await run.io_bound(lambda: _ssh_exec_wrapper(server_conf, cmd))

                    if success:
                        match = re.search(r'DEPLOY_SUCCESS_LINK: (vless://.*)', output)
                        if match:
                            link = match.group(1).strip()
                            log_area.push("✅ 部署成功！正在保存节点...")
                            final_remark = remark_input.value.strip() or f"Reality-{target_domain}"
                            node = parse_vless_link_to_node(link, final_remark)

                            if node:
                                if 'custom_nodes' not in server_conf: server_conf['custom_nodes'] = []
                                server_conf['custom_nodes'].append(node)
                                await save_servers()
                                safe_notify("✅ 节点已添加", "positive")
                                await asyncio.sleep(1);
                                d.close()
                                if callback: await callback()
                            else:
                                log_area.push("❌ 链接解析失败")
                        else:
                            log_area.push("❌ 未捕获链接，请检查日志");
                            log_area.push(output[-500:])
                    else:
                        log_area.push(f"❌ SSH 执行错误: {output}")
                except Exception as e:
                    log_area.push(f"❌ 异常: {e}")
                finally:
                    btn_deploy.props(remove='loading');
                    btn_cancel.enable()

            btn_deploy = ui.button('开始部署', on_click=run_deploy).classes('bg-red-600 text-white shadow-lg')
    d.open()


# ================= 部署 Hysteria 2 =================
async def open_deploy_hysteria_dialog(server_conf, callback):
    target_host = server_conf.get('ssh_host') or \
                  server_conf.get('url', '').replace('http://', '').replace('https://', '').split(':')[0]

    with ui.dialog() as d, ui.card().classes('w-[500px] p-0 gap-0 overflow-hidden rounded-xl shadow-2xl'):
        with ui.column().classes('w-full bg-slate-900 p-6 gap-2'):
            with ui.row().classes('items-center gap-2 text-white'):
                ui.icon('bolt', size='md');
                ui.label('部署 Hysteria 2 (Surge兼容)').classes('text-lg font-bold')
            ui.label(f"服务器 IP: {target_host}").classes('text-xs text-gray-400 font-mono')

        with ui.column().classes('w-full p-6 gap-4'):
            name_inp = ui.input('节点名称 (可选)', placeholder='例如: 狮城 Hy2').props('outlined dense').classes(
                'w-full')
            sni_inp = ui.input('伪装域名 (SNI)', value='www.bing.com').props('outlined dense').classes('w-full')

            enable_hop = ui.checkbox('启用端口跳跃', value=True).classes('text-sm font-bold text-gray-600')
            with ui.row().classes('w-full items-center gap-2'):
                hop_start = ui.number('起始端口', value=20000, format='%.0f').classes('flex-1').bind_visibility_from(
                    enable_hop, 'value')
                ui.label('-').bind_visibility_from(enable_hop, 'value')
                hop_end = ui.number('结束端口', value=50000, format='%.0f').classes('flex-1').bind_visibility_from(
                    enable_hop, 'value')

            log_area = ui.log().classes(
                'w-full h-48 bg-gray-900 text-green-400 text-[11px] font-mono p-3 rounded border border-gray-700 hidden transition-all')

        with ui.row().classes('w-full p-4 bg-gray-50 border-t border-gray-200 justify-end gap-3'):
            btn_cancel = ui.button('取消', on_click=d.close).props('flat color=grey')

            async def run_deploy():
                btn_cancel.disable();
                btn_deploy.props('loading');
                log_area.classes(remove='hidden')
                try:
                    pwd = str(uuid.uuid4()).replace('-', '')[:16]
                    params = {
                        "password": pwd, "sni": sni_inp.value,
                        "enable_hopping": "true" if enable_hop.value else "false",
                        "port_range_start": int(hop_start.value), "port_range_end": int(hop_end.value)
                    }
                    script = HYSTERIA_INSTALL_SCRIPT_TEMPLATE.format(**params)
                    cmd = f"cat > /tmp/install_hy2.sh << 'EOF_SCRIPT'\n{script}\nEOF_SCRIPT\nbash /tmp/install_hy2.sh"

                    log_area.push("🚀 [SSH] 连接并开始安装...")
                    success, output = await run.io_bound(lambda: _ssh_exec_wrapper(server_conf, cmd))

                    if success:
                        match = re.search(r'HYSTERIA_DEPLOY_SUCCESS_LINK: (hy2://.*)', output)
                        if match:
                            link = match.group(1).strip()
                            log_area.push("🎉 部署成功！")
                            node_name = name_inp.value.strip() or f"Hy2-{target_host[-3:]}"

                            if '#' in link: link = link.split('#')[0]
                            final_link = f"{link}#{urllib.parse.quote(node_name)}"

                            new_node = {
                                "id": str(uuid.uuid4()), "remark": node_name, "port": 443, "protocol": "hysteria2",
                                "settings": {}, "streamSettings": {}, "enable": True, "_is_custom": True,
                                "_raw_link": final_link
                            }
                            if 'custom_nodes' not in server_conf: server_conf['custom_nodes'] = []
                            server_conf['custom_nodes'].append(new_node)
                            await save_servers()
                            safe_notify(f"✅ 节点 {node_name} 已添加", "positive")
                            await asyncio.sleep(1);
                            d.close()
                            if callback: await callback()
                        else:
                            log_area.push("❌ 未捕获链接"); log_area.push(output[-500:])
                    else:
                        log_area.push(f"❌ SSH 失败: {output}")
                except Exception as e:
                    log_area.push(f"❌ 异常: {e}")
                finally:
                    btn_deploy.props(remove='loading'); btn_cancel.enable()

            btn_deploy = ui.button('开始部署', on_click=run_deploy).classes('bg-purple-600 text-white shadow-lg')
    d.open()