# ui_layout.py
import asyncio
import json
import time
import random
import uuid
import base64
import io
import re
from urllib.parse import urlparse, quote, parse_qs
import qrcode
import pyotp
from nicegui import ui, app, run

import config
import state
import utils
import logic

# 内容容器引用
content_container = None
# ================= 1. 辅助工具 =================
def safe_notify(message, type='info', timeout=3000):
    try: ui.notify(message, type=type, timeout=timeout)
    except: pass

async def safe_copy_to_clipboard(text):
    safe_text = json.dumps(text).replace('"', '\\"')
    js_code = f"""
    (async () => {{
        const text = {json.dumps(text)};
        try {{
            await navigator.clipboard.writeText(text);
            return true;
        }} catch (err) {{
            // 兼容性回退
            const textArea = document.createElement("textarea");
            textArea.value = text;
            textArea.style.position = "fixed";
            document.body.appendChild(textArea);
            textArea.focus();
            textArea.select();
            try {{
                document.execCommand('copy');
                document.body.removeChild(textArea);
                return true;
            }} catch (err2) {{
                document.body.removeChild(textArea);
                return false;
            }}
        }}
    }})()
    """
    try:
        result = await ui.run_javascript(js_code)
        if result: safe_notify('已复制到剪贴板', 'positive')
        else: safe_notify('复制失败', 'negative')
    except: safe_notify('复制功能不可用', 'negative')

def show_loading(container):
    try:
        container.clear()
        with container:
            with ui.column().classes('w-full h-[60vh] justify-center items-center'):
                ui.spinner('dots', size='3rem', color='primary')
                ui.label('数据处理中...').classes('text-gray-500 mt-4')
    except: pass


# [ui_layout.py] 补充到辅助工具区域
async def generate_smart_name(data):
    """智能生成服务器名称：国旗 + 地区"""
    base_url = data.get('url', '')
    ssh_host = data.get('ssh_host', '')

    # 1. 提取目标 IP/域名
    target = ssh_host
    if not target and base_url:
        target = base_url.replace('http://', '').replace('https://', '').split(':')[0]

    if not target: return "New Server"

    # 2. 调用后台解析 GeoIP
    flag = await logic.run_in_bg_executor(utils.get_flag_from_ip, target)
    if flag == "🏳️": return f"Server {target}"

    # 3. 尝试获取国家名映射
    country_name = "Unknown"
    for f, c in config.AUTO_COUNTRY_MAP.items():
        if f == flag: country_name = c.split(' ')[1] if ' ' in c else c; break

    return f"{flag} {country_name}"

# ================= WebSSH 类 =================
class WebSSH:
    def __init__(self, container, server_data):
        self.container = container
        self.server_data = server_data
        self.client = None
        self.channel = None
        self.active = False
        self.term_id = f'term_{uuid.uuid4().hex}'

    async def connect(self):
        with self.container:
            try:
                ui.element('div').props(f'id={self.term_id}').classes(
                    'w-full h-full bg-black rounded p-2 overflow-hidden relative')
                init_js = f"""
                try {{
                    if (window.{self.term_id}) {{
                        if (typeof window.{self.term_id}.dispose === 'function') {{ window.{self.term_id}.dispose(); }}
                        window.{self.term_id} = null;
                    }}
                    if (typeof Terminal === 'undefined') {{ throw new Error("xterm.js 库未加载"); }}
                    var term = new Terminal({{
                        cursorBlink: true, fontSize: 13, fontFamily: 'Menlo, Monaco, "Courier New", monospace',
                        theme: {{ background: '#000000', foreground: '#ffffff' }}, convertEol: true, scrollback: 5000
                    }});
                    var fitAddon;
                    if (typeof FitAddon !== 'undefined') {{
                        var FitAddonClass = FitAddon.FitAddon || FitAddon;
                        fitAddon = new FitAddonClass();
                        term.loadAddon(fitAddon);
                    }}
                    var el = document.getElementById('{self.term_id}');
                    term.open(el);
                    term.write('\\x1b[32m[Local] Terminal Ready. Connecting...\\x1b[0m\\r\\n');
                    if (fitAddon) {{ setTimeout(() => {{ fitAddon.fit(); }}, 200); }}
                    window.{self.term_id} = term;
                    term.focus();
                    term.onData(data => {{ emitEvent('term_input_{self.term_id}', data); }});
                    if (fitAddon) {{ new ResizeObserver(() => fitAddon.fit()).observe(el); }}
                }} catch(e) {{ console.error("Terminal Init Error:", e); }}
                """
                ui.run_javascript(init_js)
                ui.on(f'term_input_{self.term_id}', lambda e: self._write_to_ssh(e.args))

                self.client, msg = await run.io_bound(utils.get_ssh_client_sync, self.server_data)

                if not self.client:
                    self._print_error(msg);
                    return

                def pre_login_tasks():
                    last_login_msg = ""
                    try:
                        self.client.exec_command("touch ~/.hushlogin")
                        stdin, stdout, stderr = self.client.exec_command("last -n 2 -a | head -n 2 | tail -n 1")
                        raw_log = stdout.read().decode().strip()
                        if raw_log and "wtmp" not in raw_log:
                            parts = raw_log.split()
                            if len(parts) >= 7:
                                date_time = " ".join(parts[2:6]);
                                ip_addr = parts[-1]
                                last_login_msg = f"Last login:  {date_time}   {ip_addr}"
                    except:
                        pass
                    return last_login_msg

                login_info = await run.io_bound(pre_login_tasks)
                if login_info:
                    formatted_msg = f"\r\n\x1b[32m{login_info}\x1b[0m\r\n"
                    b64_msg = base64.b64encode(formatted_msg.encode('utf-8')).decode('utf-8')
                    ui.run_javascript(f'if(window.{self.term_id}) window.{self.term_id}.write(atob("{b64_msg}"));')

                self.channel = self.client.invoke_shell(term='xterm', width=100, height=30)
                self.channel.settimeout(0.0)
                self.active = True
                asyncio.create_task(self._read_loop())
                ui.notify(f"已连接到 {self.server_data['name']}", type='positive')

            except Exception as e:
                self._print_error(f"初始化异常: {e}")

    def _print_error(self, msg):
        try:
            js_cmd = f'if(window.{self.term_id}) window.{self.term_id}.write("\\r\\n\\x1b[31m[Error] {str(msg)}\\x1b[0m\\r\\n");'
            with self.container.client:
                ui.run_javascript(js_cmd)
        except:
            ui.notify(msg, type='negative')

    def _write_to_ssh(self, data):
        if self.channel and self.active:
            try:
                self.channel.send(data)
            except:
                pass

    async def _read_loop(self):
        while self.active:
            try:
                if self.channel.recv_ready():
                    data = self.channel.recv(4096)
                    if not data: break
                    b64_data = base64.b64encode(data).decode('utf-8')
                    js_cmd = f"""
                    if(window.{self.term_id}) {{
                        try {{
                            var binaryStr = atob("{b64_data}");
                            var bytes = new Uint8Array(binaryStr.length);
                            for (var i = 0; i < binaryStr.length; i++) {{ bytes[i] = binaryStr.charCodeAt(i); }}
                            var decodedStr = new TextDecoder("utf-8").decode(bytes);
                            window.{self.term_id}.write(decodedStr);
                        }} catch(e) {{ console.error("Term Decode Error", e); }}
                    }}
                    """
                    with self.container.client:
                        ui.run_javascript(js_cmd)
                await asyncio.sleep(0.01)
            except:
                await asyncio.sleep(0.1)

    def close(self):
        self.active = False
        if self.client:
            try:
                self.client.close()
            except:
                pass
        try:
            with self.container.client:
                ui.run_javascript(f'if(window.{self.term_id}) window.{self.term_id}.dispose();')
        except:
            pass


# ================= Cloudflare 设置弹窗 =================
def open_cloudflare_settings_dialog():
    with ui.dialog() as d, ui.card().classes('w-[500px] p-6 flex flex-col gap-4'):
        with ui.row().classes('items-center gap-2 text-orange-600 mb-2'):
            ui.icon('cloud', size='md');
            ui.label('Cloudflare API 配置').classes('text-lg font-bold')
        ui.label('用于自动解析域名、开启 CDN 和设置 SSL (Flexible)。').classes('text-xs text-gray-500')
        cf_token = ui.input('API Token', value=state.ADMIN_CONFIG.get('cf_api_token', '')).props(
            'outlined dense type=password').classes('w-full')
        ui.label('权限要求: Zone.DNS (Edit), Zone.Settings (Edit)').classes('text-[10px] text-gray-400 ml-1')
        cf_domain_root = ui.input('根域名 (例如: example.com)',
                                  value=state.ADMIN_CONFIG.get('cf_root_domain', '')).props('outlined dense').classes(
            'w-full')

        async def save_cf():
            state.ADMIN_CONFIG['cf_api_token'] = cf_token.value.strip()
            state.ADMIN_CONFIG['cf_root_domain'] = cf_domain_root.value.strip()
            await logic.save_admin_config()
            safe_notify('✅ Cloudflare 配置已保存', 'positive');
            d.close()

        with ui.row().classes('w-full justify-end mt-4'):
            ui.button('取消', on_click=d.close).props('flat color=grey')
            ui.button('保存配置', on_click=save_cf).classes('bg-orange-600 text-white shadow-md')
    d.open()


# ================= 全局 SSH 密钥设置弹窗 =================
def open_global_settings_dialog():
    with ui.dialog() as d, ui.card().classes('w-full max-w-2xl p-6 flex flex-col gap-4'):
        with ui.row().classes('justify-between items-center w-full border-b pb-2'):
            ui.label('🔐 全局 SSH 密钥设置').classes('text-xl font-bold')
            ui.button(icon='close', on_click=d.close).props('flat round dense color=grey')
        with ui.column().classes('w-full mt-2'):
            ui.label('全局 SSH 私钥').classes('text-sm font-bold text-gray-700')
            ui.label('当服务器未单独配置密钥时，默认使用此密钥连接。').classes('text-xs text-gray-400 mb-2')
            key_input = ui.textarea(placeholder='-----BEGIN OPENSSH PRIVATE KEY-----',
                                    value=utils.load_global_key()).classes('w-full font-mono text-xs').props(
                'outlined rows=10')

        async def save_all():
            utils.save_global_key(key_input.value)
            safe_notify('✅ 全局密钥已保存', 'positive');
            d.close()

        ui.button('保存密钥', icon='save', on_click=save_all).classes(
            'w-full bg-slate-900 text-white shadow-lg h-12 mt-2')
    d.open()


# ================= 部署 XHTTP =================
async def open_deploy_xhttp_dialog(server_conf, callback):
    target_host = server_conf.get('ssh_host') or \
                  server_conf.get('url', '').replace('http://', '').replace('https://', '').split(':')[0]
    real_ip = target_host
    import re, socket
    if not re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", target_host):
        try:
            real_ip = await run.io_bound(socket.gethostbyname, target_host)
        except:
            safe_notify(f"❌ 无法解析 IP: {target_host}", "negative"); return

    cf_handler = utils.CloudflareHandler()
    if not cf_handler.token or not cf_handler.root_domain:
        safe_notify("❌ 请先配置 Cloudflare API 和根域名", "negative");
        return

    import random, string
    rand_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
    sub_prefix = f"node-{real_ip.replace('.', '-')}-{rand_suffix}"
    target_domain = f"{sub_prefix}.{cf_handler.root_domain}"

    with ui.dialog() as d, ui.card().classes('w-[500px] p-0 gap-0 overflow-hidden rounded-xl'):
        with ui.column().classes('w-full bg-slate-900 p-6 gap-2'):
            with ui.row().classes('items-center gap-2 text-white'):
                ui.icon('rocket_launch', size='md');
                ui.label('部署 XHTTP-Reality (V76 稳定版)').classes('text-lg font-bold')
            ui.label(f"部署目标: {target_domain}").classes('text-xs text-green-400 font-mono')

        with ui.column().classes('w-full p-6 gap-4'):
            ui.label('节点备注名称').classes('text-xs font-bold text-gray-500 mb-[-8px]')
            remark_input = ui.input(placeholder=f'默认: Reality-{target_domain}').props(
                'outlined dense clearable').classes('w-full')
            log_area = ui.log().classes(
                'w-full h-48 bg-gray-900 text-green-400 text-[11px] font-mono p-3 rounded border border-gray-700 hidden transition-all')

        with ui.row().classes('w-full p-4 bg-gray-50 border-t border-gray-200 justify-end gap-3'):
            btn_cancel = ui.button('取消', on_click=d.close).props('flat color=grey')

            async def run_deploy_script():
                try:
                    log_area.push(f"🔄 [Cloudflare] 添加解析: {target_domain} -> {real_ip}...")
                    success, msg = await cf_handler.auto_configure(real_ip, sub_prefix)
                    if not success: raise Exception(f"CF配置失败: {msg}")
                    log_area.push(f"🚀 [SSH] 开始执行 V76 部署脚本...")
                    deploy_cmd = f"cat > /tmp/install_xhttp.sh << 'EOF_SCRIPT'\n{config.XHTTP_INSTALL_SCRIPT_TEMPLATE}\nEOF_SCRIPT\nbash /tmp/install_xhttp.sh \"{target_domain}\""
                    success, output = await run.io_bound(lambda: utils._ssh_exec_wrapper(server_conf, deploy_cmd))

                    if success:
                        match = re.search(r'DEPLOY_SUCCESS_LINK: (vless://.*)', output)
                        if match:
                            link = match.group(1).strip();
                            log_area.push("✅ 部署成功！正在保存节点...")
                            final_remark = remark_input.value.strip() if remark_input.value.strip() else f"Reality-{target_domain}"
                            node_data = utils.parse_vless_link_to_node(link, remark_override=final_remark)
                            if node_data:
                                if 'custom_nodes' not in server_conf: server_conf['custom_nodes'] = []
                                server_conf['custom_nodes'].append(node_data)
                                await logic.save_servers()
                                safe_notify(f"✅ 节点已添加", "positive");
                                await asyncio.sleep(1);
                                d.close()
                                if callback: await callback()
                            else:
                                log_area.push("❌ 链接解析失败")
                        else:
                            log_area.push("❌ 未捕获到链接，请检查日志"); log_area.push(output[-500:])
                    else:
                        log_area.push(f"❌ SSH 执行出错: {output}")
                except Exception as e:
                    log_area.push(f"❌ 异常: {str(e)}")
                finally:
                    btn_deploy.props(remove='loading'); btn_cancel.enable()

            async def start_process():
                btn_cancel.disable();
                btn_deploy.props('loading');
                log_area.classes(remove='hidden')
                log_area.push("🔍 正在检查端口占用情况 (80/443)...")
                check_cmd = "netstat -tlpn | grep -E ':80 |:443 ' || lsof -i :80 -i :443"
                is_occupied = False;
                check_output = ""
                try:
                    success, output = await run.io_bound(lambda: utils._ssh_exec_wrapper(server_conf, check_cmd))
                    if success and output.strip(): is_occupied = True; check_output = output.strip()
                except:
                    pass

                if is_occupied:
                    log_area.push("⚠️ 检测到端口被占用！等待用户确认...")
                    with ui.dialog() as confirm_d, ui.card().classes(
                            'w-96 p-5 border-t-4 border-red-500 shadow-xl bg-white'):
                        with ui.row().classes('items-center gap-2 text-red-600 mb-2'):
                            ui.icon('warning', size='md');
                            ui.label('端口冲突警告').classes('font-bold text-lg')
                        ui.label('检测到 VPS 上有其他服务占用了 80 或 443 端口：').classes('text-sm text-gray-600 mb-2')
                        ui.code("\n".join(check_output.split("\n")[:5])).classes(
                            'w-full text-xs bg-gray-100 p-2 rounded mb-3')
                        ui.label('如果要继续，脚本将【强制杀掉】这些进程并霸占端口。').classes(
                            'text-xs font-bold text-red-500')
                        with ui.row().classes('w-full justify-end gap-2 mt-4'):
                            ui.button('取消部署', on_click=lambda: [confirm_d.close(), d.close()]).props(
                                'flat color=grey')

                            async def confirm_force(): confirm_d.close(); log_area.push(
                                "⚔️ 用户已确认强制霸占，继续部署..."); await run_deploy_script()

                            ui.button('强制霸占并部署', color='red', on_click=confirm_force).props('unelevated')
                    confirm_d.open()
                else:
                    log_area.push("✅ 端口空闲，直接开始部署..."); await run_deploy_script()

            btn_deploy = ui.button('开始部署', on_click=start_process).classes('bg-red-600 text-white shadow-lg')
    d.open()


# ================= 部署 Hysteria 2 =================
async def open_deploy_hysteria_dialog(server_conf, callback):
    target_host = server_conf.get('ssh_host') or \
                  server_conf.get('url', '').replace('http://', '').replace('https://', '').split(':')[0]
    real_ip = target_host
    import re, socket
    if not re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", target_host):
        try:
            real_ip = await run.io_bound(socket.gethostbyname, target_host)
        except:
            safe_notify(f"❌ 无法解析 IP: {target_host}", "negative"); return

    with ui.dialog() as d, ui.card().classes('w-[500px] p-0 gap-0 overflow-hidden rounded-xl'):
        with ui.column().classes('w-full bg-slate-900 p-6 gap-2'):
            with ui.row().classes('items-center gap-2 text-white'):
                ui.icon('bolt', size='md');
                ui.label('部署 Hysteria 2 (Surge 兼容版)').classes('text-lg font-bold')
            ui.label(f"服务器 IP: {real_ip}").classes('text-xs text-gray-400 font-mono')

        with ui.column().classes('w-full p-6 gap-4'):
            name_input = ui.input('节点名称 (可选)', placeholder='例如: 狮城 Hy2').props('outlined dense').classes(
                'w-full')
            sni_input = ui.input('伪装域名 (SNI)', value='www.bing.com').props('outlined dense').classes('w-full')
            enable_hopping = ui.checkbox('启用端口跳跃', value=True).classes('text-sm font-bold text-gray-600')
            with ui.row().classes('w-full items-center gap-2'):
                hop_start = ui.number('起始端口', value=20000, format='%.0f').classes('flex-1').bind_visibility_from(
                    enable_hopping, 'value')
                ui.label('-').bind_visibility_from(enable_hopping, 'value')
                hop_end = ui.number('结束端口', value=50000, format='%.0f').classes('flex-1').bind_visibility_from(
                    enable_hopping, 'value')
            log_area = ui.log().classes(
                'w-full h-48 bg-gray-900 text-green-400 text-[11px] font-mono p-3 rounded border border-gray-700 hidden transition-all')

        with ui.row().classes('w-full p-4 bg-gray-50 border-t border-gray-200 justify-end gap-3'):
            btn_cancel = ui.button('取消', on_click=d.close).props('flat color=grey')

            async def start_process():
                btn_cancel.disable();
                btn_deploy.props('loading');
                log_area.classes(remove='hidden')
                try:
                    hy2_password = str(uuid.uuid4()).replace('-', '')[:16]
                    params = {"password": hy2_password, "sni": sni_input.value,
                              "enable_hopping": "true" if enable_hopping.value else "false",
                              "port_range_start": int(hop_start.value), "port_range_end": int(hop_end.value)}
                    script_content = config.HYSTERIA_INSTALL_SCRIPT_TEMPLATE.format(**params)
                    deploy_cmd = f"cat > /tmp/install_hy2.sh << 'EOF_SCRIPT'\n{script_content}\nEOF_SCRIPT\nbash /tmp/install_hy2.sh"

                    log_area.push(f"🚀 [SSH] 连接到 {real_ip} 开始安装...")
                    success, output = await run.io_bound(lambda: utils._ssh_exec_wrapper(server_conf, deploy_cmd))

                    if success:
                        match = re.search(r'HYSTERIA_DEPLOY_SUCCESS_LINK: (hy2://.*)', output)
                        if match:
                            link = match.group(1).strip();
                            log_area.push("🎉 部署成功！")
                            custom_name = name_input.value.strip()
                            node_name = custom_name if custom_name else f"Hy2-{real_ip[-3:]}"
                            if '#' in link: link = link.split('#')[0]
                            final_link = f"{link}#{quote(node_name)}"
                            new_node = {"id": str(uuid.uuid4()), "remark": node_name, "port": 443,
                                        "protocol": "hysteria2", "settings": {}, "streamSettings": {}, "enable": True,
                                        "_is_custom": True, "_raw_link": final_link}
                            if 'custom_nodes' not in server_conf: server_conf['custom_nodes'] = []
                            server_conf['custom_nodes'].append(new_node)
                            await logic.save_servers()
                            safe_notify(f"✅ 节点 {node_name} 已添加", "positive");
                            await asyncio.sleep(1);
                            d.close()
                            if callback: await callback()
                        else:
                            log_area.push("❌ 未捕获链接"); log_area.push(output[-500:])
                    else:
                        log_area.push(f"❌ SSH 失败: {output}")
                except Exception as e:
                    log_area.push(f"❌ 异常: {e}")
                btn_cancel.enable();
                btn_deploy.props(remove='loading')

            btn_deploy = ui.button('开始部署', on_click=start_process).props('unelevated').classes(
                'bg-purple-600 text-white')
    d.open()


# ================= 探针与监控设置弹窗 =================
def open_probe_settings_dialog():
    with ui.dialog() as d, ui.card().classes('w-full max-w-2xl p-6 flex flex-col gap-4'):
        with ui.row().classes('justify-between items-center w-full border-b pb-2'):
            with ui.row().classes('items-center gap-2'):
                ui.icon('tune', color='primary').classes('text-xl');
                ui.label('探针与监控设置').classes('text-lg font-bold')
            ui.button(icon='close', on_click=d.close).props('flat round dense color=grey')

        with ui.scroll_area().classes('w-full h-[60vh] pr-4'):
            with ui.column().classes('w-full gap-6'):
                with ui.column().classes('w-full bg-blue-50 p-4 rounded-lg border border-blue-100'):
                    ui.label('📡 主控端外部地址 (Agent连接地址)').classes('text-sm font-bold text-blue-900')
                    ui.label('Agent 将向此地址推送数据。请填写 http://公网IP:端口 或 https://域名').classes(
                        'text-xs text-blue-700 mb-2')
                    default_url = state.ADMIN_CONFIG.get('manager_base_url', 'http://xui-manager:8080')
                    url_input = ui.input(value=default_url, placeholder='http://1.2.3.4:8080').classes(
                        'w-full bg-white').props('outlined dense')

                with ui.column().classes('w-full'):
                    ui.label('🚀 三网延迟测速目标 (Ping)').classes('text-sm font-bold text-gray-700')
                    ui.label('修改后需点击“更新探针”才能在服务器上生效。').classes('text-xs text-gray-400 mb-2')
                    with ui.grid().classes('w-full grid-cols-1 sm:grid-cols-3 gap-3'):
                        ping_ct = ui.input('电信目标 IP',
                                           value=state.ADMIN_CONFIG.get('ping_target_ct', '202.102.192.68')).props(
                            'outlined dense')
                        ping_cu = ui.input('联通目标 IP',
                                           value=state.ADMIN_CONFIG.get('ping_target_cu', '112.122.10.26')).props(
                            'outlined dense')
                        ping_cm = ui.input('移动目标 IP',
                                           value=state.ADMIN_CONFIG.get('ping_target_cm', '211.138.180.2')).props(
                            'outlined dense')

                with ui.column().classes('w-full'):
                    ui.label('🤖 Telegram 通知 ').classes('text-sm font-bold text-gray-700')
                    with ui.grid().classes('w-full grid-cols-1 sm:grid-cols-2 gap-3'):
                        tg_token = ui.input('Bot Token', value=state.ADMIN_CONFIG.get('tg_bot_token', '')).props(
                            'outlined dense')
                        tg_id = ui.input('Chat ID', value=state.ADMIN_CONFIG.get('tg_chat_id', '')).props(
                            'outlined dense')

        async def save_settings():
            url_val = url_input.value.strip().rstrip('/')
            if url_val: state.ADMIN_CONFIG['manager_base_url'] = url_val
            state.ADMIN_CONFIG['ping_target_ct'] = ping_ct.value.strip()
            state.ADMIN_CONFIG['ping_target_cu'] = ping_cu.value.strip()
            state.ADMIN_CONFIG['ping_target_cm'] = ping_cm.value.strip()
            state.ADMIN_CONFIG['tg_bot_token'] = tg_token.value.strip()
            state.ADMIN_CONFIG['tg_chat_id'] = tg_id.value.strip()
            await logic.save_admin_config()
            safe_notify('✅ 设置已保存', 'positive');
            d.close()

        ui.button('保存设置', icon='save', on_click=save_settings).classes(
            'w-full bg-slate-900 text-white shadow-lg h-12')
    d.open()


# ================= 节点编辑器 =================
class InboundEditor:
    def __init__(self, mgr, data=None, on_success=None):
        self.mgr = mgr;
        self.cb = on_success;
        self.is_edit = data is not None
        if not data:
            random_port = random.randint(10000, 65000)
            self.d = {"enable": True, "remark": "", "port": random_port, "protocol": "vmess",
                      "settings": {"clients": [{"id": str(uuid.uuid4()), "alterId": 0}],
                                   "disableInsecureEncryption": False},
                      "streamSettings": {"network": "tcp", "security": "none"},
                      "sniffing": {"enabled": True, "destOverride": ["http", "tls"]}}
        else:
            self.d = data.copy()

        for k in ['settings', 'streamSettings']:
            if isinstance(self.d.get(k), str):
                try:
                    self.d[k] = json.loads(self.d[k])
                except:
                    self.d[k] = {}

    def ui(self, dlg):
        with ui.card().classes('w-full max-w-4xl p-6 flex flex-col gap-4'):
            title = '编辑节点' if self.is_edit else '新建节点'
            with ui.row().classes('justify-between items-center'):
                ui.label(title).classes('text-xl font-bold')
                ui.button(icon='close', on_click=dlg.close).props('flat round dense color=grey')

            with ui.row().classes('w-full gap-4'):
                self.rem = ui.input('备注', value=self.d.get('remark')).classes('flex-grow')
                self.ena = ui.switch('启用', value=self.d.get('enable', True)).classes('mt-2')

            with ui.row().classes('w-full gap-4'):
                self.pro = ui.select(['vmess', 'vless', 'trojan', 'shadowsocks', 'socks'], value=self.d['protocol'],
                                     label='协议', on_change=self.on_protocol_change).classes('w-1/3')
                self.prt = ui.number('端口', value=self.d['port'], format='%.0f').classes('w-1/3')
                ui.button(icon='shuffle', on_click=lambda: self.prt.set_value(int(random.randint(10000, 60000)))).props(
                    'flat dense').tooltip('随机端口')

            ui.separator().classes('my-2');
            self.auth_box = ui.column().classes('w-full gap-2');
            self.refresh_auth_ui();
            ui.separator().classes('my-2')

            with ui.row().classes('w-full gap-4'):
                st = self.d.get('streamSettings', {})
                self.net = ui.select(['tcp', 'ws', 'grpc'], value=st.get('network', 'tcp'), label='传输协议').classes(
                    'w-1/3')
                self.sec = ui.select(['none', 'tls'], value=st.get('security', 'none'), label='安全加密').classes(
                    'w-1/3')

            with ui.row().classes('w-full justify-end mt-6'):
                ui.button('保存', on_click=lambda: self.save(dlg)).props('color=primary')

    def on_protocol_change(self, e):
        p = e.value;
        s = self.d.get('settings', {})
        if p in ['vmess', 'vless']:
            if 'clients' not in s: self.d['settings'] = {"clients": [{"id": str(uuid.uuid4()), "alterId": 0}],
                                                         "disableInsecureEncryption": False}
        elif p == 'trojan':
            if 'clients' not in s: self.d['settings'] = {"clients": [{"password": str(uuid.uuid4().hex[:8])}]}
        elif p == 'shadowsocks':
            if 'password' not in s: self.d['settings'] = {"method": "aes-256-gcm",
                                                          "password": str(uuid.uuid4().hex[:10]), "network": "tcp,udp"}
        elif p == 'socks':
            if 'accounts' not in s: self.d['settings'] = {"auth": "password",
                                                          "accounts": [{"user": "admin", "pass": "admin"}],
                                                          "udp": False}
        self.d['protocol'] = p;
        self.refresh_auth_ui()

    def refresh_auth_ui(self):
        self.auth_box.clear();
        p = self.pro.value;
        s = self.d.get('settings', {})
        with self.auth_box:
            if p in ['vmess', 'vless']:
                clients = s.get('clients', [{}]);
                cid = clients[0].get('id', str(uuid.uuid4()))
                ui.label('认证 (UUID)').classes('text-sm font-bold text-gray-500')
                uuid_inp = ui.input('UUID', value=cid).classes('w-full').on_value_change(
                    lambda e: s['clients'][0].update({'id': e.value}))
                ui.button('生成 UUID', on_click=lambda: uuid_inp.set_value(str(uuid.uuid4()))).props(
                    'flat dense size=sm')
            elif p == 'trojan':
                clients = s.get('clients', [{}]);
                pwd = clients[0].get('password', '')
                ui.input('密码', value=pwd).classes('w-full').on_value_change(
                    lambda e: s['clients'][0].update({'password': e.value}))
            elif p == 'shadowsocks':
                method = s.get('method', 'aes-256-gcm');
                pwd = s.get('password', '')
                with ui.row().classes('w-full gap-4'):
                    ui.select(['aes-256-gcm', 'chacha20-ietf-poly1305', 'aes-128-gcm'], value=method,
                              label='加密').classes('flex-1').on_value_change(lambda e: s.update({'method': e.value}))
                    ui.input('密码', value=pwd).classes('flex-1').on_value_change(
                        lambda e: s.update({'password': e.value}))
            elif p == 'socks':
                accounts = s.get('accounts', [{}]);
                user = accounts[0].get('user', '');
                pwd = accounts[0].get('pass', '')
                with ui.row().classes('w-full gap-4'):
                    ui.input('用户名', value=user).classes('flex-1').on_value_change(
                        lambda e: s['accounts'][0].update({'user': e.value}))
                    ui.input('密码', value=pwd).classes('flex-1').on_value_change(
                        lambda e: s['accounts'][0].update({'pass': e.value}))

    async def save(self, dlg):
        self.d['remark'] = self.rem.value;
        self.d['enable'] = self.ena.value
        try:
            port_val = int(self.prt.value)
            if port_val <= 0 or port_val > 65535: raise ValueError
            self.d['port'] = port_val
        except:
            safe_notify("请输入有效端口", "negative"); return
        self.d['protocol'] = self.pro.value
        if 'streamSettings' not in self.d: self.d['streamSettings'] = {}
        self.d['streamSettings']['network'] = self.net.value
        self.d['streamSettings']['security'] = self.sec.value
        if 'sniffing' not in self.d: self.d['sniffing'] = {"enabled": True, "destOverride": ["http", "tls"]}

        try:
            success, msg = False, ""
            is_ssh_manager = hasattr(self.mgr, '_exec_remote_script')
            if is_ssh_manager:
                if self.is_edit:
                    success, msg = await self.mgr.update_inbound(self.d['id'], self.d)
                else:
                    success, msg = await self.mgr.add_inbound(self.d)
            else:
                if self.is_edit:
                    success, msg = await run.io_bound(self.mgr.update_inbound, self.d['id'], self.d)
                else:
                    success, msg = await run.io_bound(self.mgr.add_inbound, self.d)

            if success:
                safe_notify(f"✅ {msg}", "positive");
                dlg.close()
                if self.cb:
                    res = self.cb();
                    if asyncio.iscoroutine(res): await res
            else:
                safe_notify(f"❌ 失败: {msg}", "negative", timeout=5000)
        except Exception as e:
            safe_notify(f"❌ 系统异常: {str(e)}", "negative")


async def open_inbound_dialog(mgr, data, cb):
    with ui.dialog() as d: InboundEditor(mgr, data, cb).ui(d); d.open()


async def delete_inbound_with_confirm(mgr, inbound_id, inbound_remark, callback):
    with ui.dialog() as d, ui.card():
        ui.label('删除确认').classes('text-lg font-bold text-red-600')
        ui.label(f"您确定要永久删除节点 [{inbound_remark}] 吗？").classes('text-base mt-2')
        with ui.row().classes('w-full justify-end gap-2'):
            ui.button('取消', on_click=d.close).props('flat color=grey')

            async def do_delete(): d.close(); await logic.delete_inbound(mgr, inbound_id, callback)

            ui.button('确定删除', color='red', on_click=do_delete)
    d.open()


# ================= 订阅编辑器 =================
class AdvancedSubEditor:
    def __init__(self, sub_data=None):
        import copy
        if sub_data:
            self.sub = copy.deepcopy(sub_data)
        else:
            self.sub = {'name': '', 'token': str(uuid.uuid4()), 'nodes': [], 'options': {}}
        if 'options' not in self.sub: self.sub['options'] = {}
        self.selected_ids = list(self.sub.get('nodes', []))
        self.all_nodes_map = {};
        self.ui_groups = {};
        self.server_expansions = {};
        self.server_items = {}
        self.preview_container = None;
        self.list_container = None

    def ui(self, dlg):
        self._preload_data()
        with ui.card().classes('w-full max-w-6xl h-[90vh] flex flex-col p-0 overflow-hidden'):
            with ui.row().classes('w-full p-4 border-b bg-gray-50 justify-between items-center flex-shrink-0'):
                with ui.row().classes('items-center gap-2'):
                    ui.icon('tune', color='primary').classes('text-xl');
                    ui.label('订阅高级管理').classes('text-lg font-bold')
                ui.button(icon='close', on_click=dlg.close).props('flat round dense color=grey')

            with ui.row().classes('w-full flex-grow overflow-hidden gap-0'):
                # 左侧
                with ui.column().classes('w-2/5 h-full border-r border-gray-200 flex flex-col bg-gray-50'):
                    with ui.column().classes('w-full p-2 border-b bg-white gap-2'):
                        ui.input(placeholder='🔍 搜索源节点 (如: 日本)', on_change=self.on_search).props(
                            'outlined dense debounce="300"').classes('w-full')
                        with ui.row().classes('w-full justify-between items-center'):
                            ui.label('筛选结果操作:').classes('text-xs text-gray-400')
                            with ui.row().classes('gap-1'):
                                ui.button('全选', icon='add_circle', on_click=lambda: self.batch_select(True)).props(
                                    'unelevated dense size=sm color=blue-6')
                                ui.button('清空', icon='remove_circle',
                                          on_click=lambda: self.batch_select(False)).props(
                                    'flat dense size=sm color=grey-6')
                    with ui.scroll_area().classes('w-full flex-grow p-2'):
                        self.list_container = ui.column().classes('w-full gap-2')
                        ui.timer(0.1, lambda: asyncio.create_task(self._render_node_tree()), once=True)

                # 中间
                with ui.column().classes(
                        'w-1/4 h-full border-r border-gray-200 flex flex-col bg-white overflow-y-auto'):
                    with ui.column().classes('w-full p-4 gap-4'):
                        ui.label('① 基础设置').classes('text-xs font-bold text-blue-500 uppercase')
                        ui.input('订阅名称', value=self.sub.get('name', '')).bind_value_to(self.sub, 'name').props(
                            'outlined dense').classes('w-full')
                        with ui.row().classes('w-full gap-1'):
                            ui.input('Token', value=self.sub.get('token', '')).bind_value_to(self.sub, 'token').props(
                                'outlined dense').classes('flex-grow')
                            ui.button(icon='refresh',
                                      on_click=lambda: self.sub.update({'token': str(uuid.uuid4())[:8]})).props(
                                'flat dense')
                        ui.separator()
                        ui.label('② 排序工具').classes('text-xs font-bold text-blue-500 uppercase')
                        with ui.grid().classes('w-full grid-cols-2 gap-2'):
                            ui.button('名称 A-Z', on_click=lambda: self.sort_nodes('name_asc')).props(
                                'outline dense size=sm')
                            ui.button('名称 Z-A', on_click=lambda: self.sort_nodes('name_desc')).props(
                                'outline dense size=sm')
                            ui.button('随机打乱', on_click=lambda: self.sort_nodes('random')).props(
                                'outline dense size=sm')
                            ui.button('列表倒序', on_click=lambda: self.sort_nodes('reverse')).props(
                                'outline dense size=sm')
                        ui.separator()
                        ui.label('③ 批量重命名').classes('text-xs font-bold text-blue-500 uppercase')
                        with ui.column().classes('w-full gap-2 bg-blue-50 p-2 rounded border border-blue-100'):
                            opt = self.sub.get('options', {})
                            pat = ui.input('正则 (如: ^)', value=opt.get('rename_pattern', '')).props(
                                'outlined dense bg-white').classes('w-full')
                            rep = ui.input('替换 (如: VIP-)', value=opt.get('rename_replacement', '')).props(
                                'outlined dense bg-white').classes('w-full')

                            def apply_regex():
                                self.sub['options']['rename_pattern'] = pat.value;
                                self.sub['options']['rename_replacement'] = rep.value
                                self.update_preview();
                                safe_notify('预览已刷新', 'positive')

                            ui.button('刷新预览', on_click=apply_regex).props(
                                'unelevated dense size=sm color=blue').classes('w-full')

                # 右侧
                with ui.column().classes('w-[35%] h-full bg-slate-50 flex flex-col'):
                    with ui.row().classes('w-full p-3 border-b bg-white items-center justify-between shadow-sm z-10'):
                        ui.label('已选节点清单').classes('font-bold text-gray-800')
                        with ui.row().classes('items-center gap-2'):
                            ui.label('').bind_text_from(self, 'selected_ids', lambda x: f"{len(x)}")
                            ui.button('清空全部', icon='delete_forever', on_click=self.clear_all_selected).props(
                                'flat dense size=sm color=red')
                    with ui.scroll_area().classes('w-full flex-grow p-2'):
                        self.preview_container = ui.column().classes('w-full gap-1')
                        self.update_preview()

            with ui.row().classes('w-full p-3 border-t bg-gray-100 justify-end gap-3 flex-shrink-0'):
                async def save_all():
                    if not self.sub.get('name'): return safe_notify('名称不能为空', 'negative')
                    self.sub['nodes'] = self.selected_ids
                    found = False
                    for i, s in enumerate(state.SUBS_CACHE):
                        if s.get('token') == self.sub['token']:
                            state.SUBS_CACHE[i] = self.sub;
                            found = True;
                            break
                    if not found: state.SUBS_CACHE.append(self.sub)
                    await logic.save_subs();
                    await load_subs_view()
                    dlg.close();
                    safe_notify('✅ 订阅保存成功', 'positive')

                ui.button('保存配置', icon='save', on_click=save_all).classes('bg-slate-800 text-white shadow-lg')

    def _preload_data(self):
        self.all_nodes_map = {}
        for srv in state.SERVERS_CACHE:
            nodes = (state.NODES_DATA.get(srv['url'], []) or []) + srv.get('custom_nodes', [])
            for n in nodes:
                key = f"{srv['url']}|{n['id']}";
                n['_server_name'] = srv['name'];
                self.all_nodes_map[key] = n

    async def _render_node_tree(self):
        self.list_container.clear();
        self.ui_groups = {};
        self.server_expansions = {};
        self.server_items = {}
        grouped = {}
        for srv in state.SERVERS_CACHE:
            nodes = (state.NODES_DATA.get(srv['url'], []) or []) + srv.get('custom_nodes', [])
            if not nodes: continue
            g_name = srv.get('group', '默认分组')
            try:
                if g_name in ['默认分组', '自动注册']: g_name = logic.detect_country_group(srv.get('name'), srv)
            except:
                pass
            if g_name not in grouped: grouped[g_name] = []
            grouped[g_name].append({'server': srv, 'nodes': nodes})

        with self.list_container:
            for i, g_name in enumerate(sorted(grouped.keys())):
                if i % 2 == 0: await asyncio.sleep(0.01)
                exp = ui.expansion(g_name, icon='folder', value=True).classes(
                    'w-full border rounded bg-white shadow-sm mb-1').props(
                    'header-class="bg-gray-100 text-sm font-bold p-2 min-h-0"')
                self.server_expansions[g_name] = exp;
                self.server_items[g_name] = []
                with exp:
                    with ui.column().classes('w-full p-2 gap-2'):
                        for item in grouped[g_name]:
                            srv = item['server'];
                            search_key = f"{srv['name']}".lower()
                            container = ui.column().classes('w-full gap-1')
                            with container:
                                server_header = ui.row().classes('w-full items-center gap-1 mt-1 px-1')
                                with server_header:
                                    ui.icon('dns', size='xs').classes('text-blue-400');
                                    ui.label(srv['name']).classes('text-xs font-bold text-gray-500 truncate')
                                for n in item['nodes']:
                                    key = f"{srv['url']}|{n['id']}";
                                    is_checked = key in self.selected_ids
                                    self.server_items[g_name].append(key)
                                    with ui.row().classes(
                                            'w-full items-center pl-2 py-1 hover:bg-blue-50 rounded cursor-pointer transition border border-transparent') as row:
                                        chk = ui.checkbox(value=is_checked).props('dense size=xs');
                                        chk.disable()
                                        row.on('click', lambda _, k=key: self.toggle_node_from_left(k))
                                        ui.label(n.get('remark', '未命名')).classes(
                                            'text-xs text-gray-700 truncate flex-grow')
                                        full_text = f"{search_key} {n.get('remark', '')} {n.get('protocol', '')}".lower()
                                        self.ui_groups[key] = {'row': row, 'chk': chk, 'text': full_text,
                                                               'group_name': g_name, 'header': server_header,
                                                               'container': container}

    def toggle_node_from_left(self, key):
        if key in self.selected_ids:
            self.remove_node(key)
        else:
            self.selected_ids.append(key);
            self.update_preview()
            if key in self.ui_groups:
                self.ui_groups[key]['chk'].value = True
                self.ui_groups[key]['row'].classes(add='bg-blue-50 border-blue-200', remove='border-transparent')

    def remove_node(self, key):
        if key in self.selected_ids:
            self.selected_ids.remove(key);
            self.update_preview()
            if key in self.ui_groups:
                self.ui_groups[key]['chk'].value = False
                self.ui_groups[key]['row'].classes(remove='bg-blue-50 border-blue-200', add='border-transparent')

    def clear_all_selected(self):
        for key in list(self.selected_ids): self.remove_node(key)

    def update_preview(self):
        self.preview_container.clear()
        pat = self.sub.get('options', {}).get('rename_pattern', '')
        rep = self.sub.get('options', {}).get('rename_replacement', '')
        with self.preview_container:
            if not self.selected_ids:
                with ui.column().classes('w-full items-center mt-10 text-gray-300 gap-2'):
                    ui.icon('shopping_cart', size='3rem');
                    ui.label('清单为空')
                return
            with ui.column().classes('w-full gap-1'):
                for idx, key in enumerate(self.selected_ids):
                    node = self.all_nodes_map.get(key)
                    if not node: continue
                    orig_name = node.get('remark', 'Unknown');
                    final_name = orig_name
                    if pat:
                        try:
                            import re; final_name = re.sub(pat, rep, orig_name)
                        except:
                            pass
                    with ui.row().classes(
                            'w-full items-center p-1.5 bg-white border border-gray-200 rounded shadow-sm group hover:border-red-300 transition'):
                        ui.label(str(idx + 1)).classes('text-[10px] text-gray-400 w-5 text-center')
                        chk = ui.checkbox(value=True).props('dense size=xs color=green')
                        chk.on_value_change(lambda e, k=key: self.remove_node(k) if not e.value else None)
                        with ui.column().classes('gap-0 leading-none flex-grow ml-1'):
                            if final_name != orig_name:
                                ui.label(final_name).classes('text-xs font-bold text-blue-600')
                                ui.label(orig_name).classes('text-[9px] text-gray-400 line-through')
                            else:
                                ui.label(final_name).classes('text-xs font-bold text-gray-700')
                        ui.button(icon='close', on_click=lambda _, k=key: self.remove_node(k)).props(
                            'flat dense size=xs color=red').classes('opacity-0 group-hover:opacity-100')

    def sort_nodes(self, mode):
        if not self.selected_ids: return safe_notify('列表为空', 'warning')
        objs = []
        for k in self.selected_ids:
            n = self.all_nodes_map.get(k)
            if n: objs.append({'key': k, 'name': n.get('remark', '').lower()})
        if mode == 'name_asc':
            objs.sort(key=lambda x: x['name'])
        elif mode == 'name_desc':
            objs.sort(key=lambda x: x['name'], reverse=True)
        elif mode == 'random':
            random.shuffle(objs)
        elif mode == 'reverse':
            objs.reverse()
        self.selected_ids = [x['key'] for x in objs];
        self.update_preview()
        safe_notify(f'已按 {mode} 重新排序', 'positive')

    def on_search(self, e):
        txt = str(e.value).lower().strip()
        visible_groups = set();
        visible_headers = set()
        for key, item in self.ui_groups.items():
            visible = (not txt) or (txt in item['text'])
            item['row'].set_visibility(visible)
            if visible:
                visible_groups.add(item['group_name']);
                visible_headers.add(item['header'])
        for g_name, exp in self.server_expansions.items():
            is_visible = g_name in visible_groups;
            exp.set_visibility(is_visible)
            if txt and is_visible: exp.value = True
        all_headers = set(item['header'] for item in self.ui_groups.values())
        for header in all_headers: header.set_visibility(header in visible_headers)

    def batch_select(self, val):
        count = 0
        for key, item in self.ui_groups.items():
            if item['row'].visible:
                if val and key not in self.selected_ids:
                    self.selected_ids.append(key);
                    item['chk'].value = True;
                    item['row'].classes(add='bg-blue-50 border-blue-200', remove='border-transparent');
                    count += 1
                elif not val and key in self.selected_ids:
                    self.selected_ids.remove(key);
                    item['chk'].value = False;
                    item['row'].classes(remove='bg-blue-50 border-blue-200', add='border-transparent');
                    count += 1
        if count > 0:
            self.update_preview(); safe_notify(f"已{'添加' if val else '移除'} {count} 个节点", "positive")
        else:
            safe_notify("当前没有可操作的节点", "warning")


def open_advanced_sub_editor(sub_data=None):
    with ui.dialog() as d: AdvancedSubEditor(sub_data).ui(d); d.open()


# ================= 8. 分组管理弹窗函数 =================

def open_quick_group_create_dialog(callback=None):
    selection_map = {s['url']: False for s in state.SERVERS_CACHE}
    ui_rows = {}
    with ui.dialog() as d, ui.card().classes('w-full max-w-lg h-[85vh] flex flex-col p-0'):
        with ui.column().classes('w-full p-4 border-b bg-gray-50 gap-3 flex-shrink-0'):
            with ui.row().classes('w-full justify-between items-center'):
                ui.label('新建分组 (标签模式)').classes('text-lg font-bold')
                ui.button(icon='close', on_click=d.close).props('flat round dense color=grey')
            name_input = ui.input('分组名称', placeholder='例如: 甲骨文云').props('outlined dense autofocus').classes(
                'w-full bg-white')
            search_input = ui.input(placeholder='🔍 搜索筛选...').props('outlined dense clearable').classes(
                'w-full bg-white')

            def on_search(e):
                kw = str(e.value).lower().strip()
                for url, item in ui_rows.items(): item['row'].set_visibility(kw in item['search_text'])

            search_input.on_value_change(on_search)

        with ui.column().classes('w-full flex-grow overflow-hidden relative'):
            with ui.row().classes('w-full p-2 bg-gray-100 justify-between items-center border-b flex-shrink-0'):
                ui.label('勾选加入:').classes('text-xs font-bold text-gray-500 ml-2')
                with ui.row().classes('gap-1'):
                    ui.button('全选 (当前)', on_click=lambda: toggle_visible(True)).props(
                        'flat dense size=xs color=primary')
                    ui.button('清空', on_click=lambda: toggle_visible(False)).props('flat dense size=xs color=grey')

            with ui.scroll_area().classes('w-full flex-grow p-2'):
                with ui.column().classes('w-full gap-1'):
                    try:
                        sorted_srv = sorted(state.SERVERS_CACHE, key=lambda x: str(x.get('name', '')))
                    except:
                        sorted_srv = state.SERVERS_CACHE
                    for s in sorted_srv:
                        search_key = f"{s['name']} {s['url']}".lower()
                        with ui.row().classes(
                                'w-full items-center p-2 hover:bg-blue-50 rounded border border-transparent hover:border-blue-200 transition cursor-pointer') as row:
                            chk = ui.checkbox(value=False).props('dense');
                            chk.on('click.stop', lambda: None)
                            chk.on_value_change(lambda e, u=s['url']: selection_map.update({u: e.value}))
                            row.on('click', lambda _, c=chk: c.set_value(not c.value))
                            ui.label(s['name']).classes(
                                'text-sm font-bold text-gray-700 ml-2 truncate flex-grow select-none')
                            ui.label(logic.detect_country_group(s['name'], s)).classes(
                                'text-xs text-gray-400 font-mono')
                        ui_rows[s['url']] = {'row': row, 'chk': chk, 'search_text': search_key}

            def toggle_visible(state):
                cnt = 0
                for item in ui_rows.values():
                    if item['row'].visible: item['chk'].value = state; cnt += 1
                if state and cnt > 0: safe_notify(f"选中 {cnt} 个", "positive")

        async def save():
            new_name = name_input.value.strip()
            if not new_name: return safe_notify('名称不能为空', 'warning')
            existing = set(state.ADMIN_CONFIG.get('custom_groups', []))
            if new_name in existing: return safe_notify('分组已存在', 'warning')
            if 'custom_groups' not in state.ADMIN_CONFIG: state.ADMIN_CONFIG['custom_groups'] = []
            state.ADMIN_CONFIG['custom_groups'].append(new_name)
            await logic.save_admin_config()
            count = 0
            for s in state.SERVERS_CACHE:
                if selection_map.get(s['url'], False):
                    if 'tags' not in s: s['tags'] = []
                    if new_name not in s['tags']: s['tags'].append(new_name); count += 1
                    if s.get('group') == new_name: s['group'] = logic.detect_country_group(s['name'], None)
            if count > 0: await logic.save_servers()
            render_sidebar_content.refresh()
            safe_notify(f'✅ 分组创建成功', 'positive');
            d.close()
            if callback: await callback(new_name)

        with ui.row().classes('w-full p-4 border-t bg-white justify-end gap-2 flex-shrink-0'):
            ui.button('取消', on_click=d.close).props('flat color=grey')
            ui.button('创建并保存', on_click=save).classes('bg-blue-600 text-white shadow-md')
    d.open()


def open_group_sort_dialog():
    current = state.ADMIN_CONFIG.get('probe_custom_groups', [])
    if not current: return safe_notify("暂无自定义视图", "warning")
    temp = list(current)
    with ui.dialog() as d, ui.card().style(
            'width: 400px; height: 60vh; display: flex; flex-direction: column; padding: 0;'):
        with ui.row().classes('w-full p-4 border-b justify-between items-center bg-gray-50'):
            ui.label('自定义排序').classes('font-bold text-gray-700')
            ui.button(icon='close', on_click=d.close).props('flat round dense color=grey')
        list_con = ui.element('div').classes('w-full bg-slate-50 p-2 gap-2 flex-grow overflow-y-auto flex flex-col')

        def render():
            list_con.clear()
            with list_con:
                for i, name in enumerate(temp):
                    with ui.card().classes('w-full p-3 flex-row items-center gap-3 border border-gray-200 shadow-sm'):
                        ui.label(str(i + 1)).classes('text-xs text-gray-400 w-4')
                        ui.label(name).classes('font-bold text-gray-700 flex-grow text-sm')
                        with ui.row().classes('gap-1'):
                            if i > 0:
                                ui.button(icon='arrow_upward', on_click=lambda _, x=i: move(x, -1)).props(
                                    'flat dense round size=sm color=blue')
                            else:
                                ui.element('div').classes('w-8')
                            if i < len(temp) - 1:
                                ui.button(icon='arrow_downward', on_click=lambda _, x=i: move(x, 1)).props(
                                    'flat dense round size=sm color=blue')
                            else:
                                ui.element('div').classes('w-8')

        def move(idx, direction):
            tgt = idx + direction
            if 0 <= tgt < len(temp): temp[idx], temp[tgt] = temp[tgt], temp[idx]; render()

        render()

        async def save():
            state.ADMIN_CONFIG['probe_custom_groups'] = temp
            await logic.save_admin_config()
            safe_notify("✅ 顺序已更新", "positive");
            d.close()

        with ui.row().classes('w-full p-4 border-t bg-white'):
            ui.button('保存顺序', icon='save', on_click=save).classes('w-full bg-slate-900 text-white')
    d.open()


def open_unified_group_manager(mode='manage'):
    if 'probe_custom_groups' not in state.ADMIN_CONFIG: state.ADMIN_CONFIG['probe_custom_groups'] = []
    st = {'current_group': None, 'selected_urls': set(), 'checkboxes': {}, 'page': 1, 'search_text': ''}
    view_list = None;
    server_list = None;
    title_inp = None;
    pagin = None

    with ui.dialog() as d, ui.card().classes('w-full max-w-5xl h-[90vh] flex flex-col p-0 gap-0'):
        with ui.row().classes('w-full p-3 bg-slate-100 border-b items-center gap-2 overflow-x-auto flex-shrink-0'):
            ui.label('视图列表:').classes('font-bold text-gray-500 mr-2 text-xs')
            ui.button('➕ 新建', on_click=lambda: load_group(None)).props(
                'unelevated color=green text-color=white size=sm')
            ui.separator().props('vertical').classes('mx-2 h-6')
            view_list = ui.row().classes('gap-2 items-center flex-nowrap');
            ui.space()
            ui.button(icon='close', on_click=d.close).props('flat round dense color=grey')

        with ui.row().classes('w-full p-4 bg-white border-b items-center gap-4 flex-shrink-0 wrap'):
            title_inp = ui.input('视图名称').props('outlined dense').classes('min-w-[200px] flex-grow font-bold')
            ui.input(placeholder='🔍 搜索...', on_change=lambda e: update_search(e.value)).props(
                'outlined dense').classes('w-48')
            with ui.row().classes('gap-2'):
                ui.button('全选本页', on_click=lambda: toggle_page(True)).props('flat dense size=sm color=blue')
                ui.button('清空本页', on_click=lambda: toggle_page(False)).props('flat dense size=sm color=grey')

        with ui.scroll_area().classes('w-full flex-grow p-4 bg-gray-50'): server_list = ui.column().classes(
            'w-full gap-2')
        with ui.row().classes('w-full p-2 justify-center bg-gray-50 border-t border-gray-200'): pagin = ui.row()
        with ui.row().classes('w-full p-4 bg-white border-t justify-between items-center flex-shrink-0'):
            ui.button('删除此视图', icon='delete', color='red', on_click=lambda: delete_group()).props('flat')
            ui.button('保存当前配置', icon='save', on_click=lambda: save_group()).classes(
                'bg-slate-900 text-white shadow-lg')

    def update_search(val):
        st['search_text'] = str(val).lower().strip(); st['page'] = 1; render_servers()

    def render_views():
        view_list.clear();
        groups = state.ADMIN_CONFIG.get('probe_custom_groups', [])
        with view_list:
            for g in groups:
                props = 'unelevated color=blue' if g == st['current_group'] else 'outline color=grey text-color=grey-8'
                ui.button(g, on_click=lambda _, n=g: load_group(n)).props(f'{props} size=sm')

    def load_group(name):
        st['current_group'] = name;
        st['page'] = 1;
        st['selected_urls'] = set()
        if name:
            for s in state.SERVERS_CACHE:
                if (name in s.get('tags', [])) or (s.get('group') == name): st['selected_urls'].add(s['url'])
        render_views();
        title_inp.value = name if name else '';
        render_servers()

    def render_servers():
        server_list.clear();
        pagin.clear();
        st['checkboxes'] = {}
        if not state.SERVERS_CACHE:
            with server_list: ui.label('暂无服务器').classes('text-center text-gray-400 mt-10 w-full'); return

        all_srv = state.SERVERS_CACHE
        if st['search_text']: all_srv = [s for s in all_srv if
                                         st['search_text'] in s.get('name', '').lower() or st['search_text'] in s.get(
                                             'url', '').lower()]
        try:
            sorted_servers = sorted(all_srv, key=lambda x: str(x.get('name', '')))
        except:
            sorted_servers = all_srv

        PAGE_SIZE = 48;
        total = len(sorted_servers);
        pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
        if st['page'] > pages: st['page'] = 1
        if st['page'] < 1: st['page'] = 1
        start = (st['page'] - 1) * PAGE_SIZE;
        end = start + PAGE_SIZE;
        current_items = sorted_servers[start:end]

        with server_list:
            ui.label(f"共 {total} 台 (第 {st['page']}/{pages} 页)").classes('text-xs text-gray-400 mb-2')
            with ui.grid().classes('w-full grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2'):
                for s in current_items:
                    url = s.get('url')
                    if not url: continue
                    is_chk = url in st['selected_urls']
                    bg = 'bg-blue-50 border-blue-300' if is_chk else 'bg-white border-gray-200'
                    with ui.row().classes(
                            f'items-center p-2 border rounded cursor-pointer hover:border-blue-400 transition {bg}') as row:
                        chk = ui.checkbox(value=is_chk).props('dense');
                        st['checkboxes'][url] = chk

                        def toggle(c=chk, r=row, u=url):
                            c.value = not c.value;
                            update_sel(u, c.value)
                            if c.value:
                                r.classes(add='bg-blue-50 border-blue-300', remove='bg-white border-gray-200')
                            else:
                                r.classes(remove='bg-blue-50 border-blue-300', add='bg-white border-gray-200')

                        row.on('click', toggle)
                        chk.on('click.stop', lambda _, c=chk, r=row, u=url: [update_sel(u, c.value),
                                                                             r.classes(add='bg-blue-50 border-blue-300',
                                                                                       remove='bg-white border-gray-200') if c.value else r.classes(
                                                                                 remove='bg-blue-50 border-blue-300',
                                                                                 add='bg-white border-gray-200')])
                        with ui.column().classes('gap-0 ml-2 overflow-hidden'):
                            ui.label(s.get('name', 'Unknown')).classes('text-sm font-bold truncate text-gray-700')
                            if is_chk:
                                ui.label('已选中').classes('text-[10px] text-blue-500 font-bold')
                            else:
                                ui.label(s.get('group', '')).classes('text-[10px] text-gray-300')
        if pages > 1:
            with pagin:
                p = ui.pagination(1, pages, direction_links=True).props('dense color=blue');
                p.value = st['page']
                p.on('update:model-value', lambda e: [st.update({'page': e.args}), render_servers()])

    def update_sel(url, chk):
        if chk:
            st['selected_urls'].add(url)
        else:
            st['selected_urls'].discard(url)

    def toggle_page(val):
        for url in st['checkboxes'].keys():
            if val:
                st['selected_urls'].add(url)
            else:
                st['selected_urls'].discard(url)
        render_servers()

    async def save_group():
        old = st['current_group'];
        new = title_inp.value.strip()
        if not new: return safe_notify("名称不能为空", "warning")
        groups = state.ADMIN_CONFIG.get('probe_custom_groups', [])
        if not old:
            if new in groups: return safe_notify("名称已存在", "negative")
            groups.append(new)
        elif new != old:
            if new in groups: return safe_notify("名称已存在", "negative")
            groups[groups.index(old)] = new
            for s in state.SERVERS_CACHE:
                if 'tags' in s and old in s['tags']: s['tags'].remove(old); s['tags'].append(new)

        for s in state.SERVERS_CACHE:
            if 'tags' not in s: s['tags'] = []
            if s['url'] in st['selected_urls']:
                if new not in s['tags']: s['tags'].append(new)
            else:
                if new in s['tags']: s['tags'].remove(new)

        state.ADMIN_CONFIG['probe_custom_groups'] = groups
        await logic.save_admin_config();
        await logic.save_servers()
        safe_notify("✅ 保存成功", "positive");
        load_group(new)
        try:
            await render_probe_page()
        except:
            pass

    async def delete_group():
        target = st['current_group']
        if not target: return
        if target in state.ADMIN_CONFIG.get('probe_custom_groups', []):
            state.ADMIN_CONFIG['probe_custom_groups'].remove(target);
            await logic.save_admin_config()
        for s in state.SERVERS_CACHE:
            if 'tags' in s and target in s['tags']: s['tags'].remove(target)
        await logic.save_servers();
        safe_notify("🗑️ 已删除", "positive");
        load_group(None)
        try:
            await render_probe_page()
        except:
            pass

    ui.timer(0.1, lambda: [render_views(), load_group(None)], once=True)
    d.open()


def open_create_group_dialog():
    with ui.dialog() as d, ui.card().classes('w-full max-w-sm flex flex-col gap-4 p-6'):
        ui.label('新建自定义分组').classes('text-lg font-bold mb-2')
        name_input = ui.input('分组名称', placeholder='例如: 生产环境').classes('w-full').props('outlined')

        async def save_new_group():
            new_name = name_input.value.strip()
            if not new_name: return safe_notify("分组名称不能为空", "warning")
            if new_name in state.ADMIN_CONFIG.get('custom_groups', []): return safe_notify("该分组已存在", "warning")

            if 'custom_groups' not in state.ADMIN_CONFIG: state.ADMIN_CONFIG['custom_groups'] = []
            state.ADMIN_CONFIG['custom_groups'].append(new_name)
            await logic.save_admin_config()
            d.close();
            render_sidebar_content.refresh();
            safe_notify(f"已创建分组: {new_name}", "positive")

        with ui.row().classes('w-full justify-end gap-2 mt-4'):
            ui.button('取消', on_click=d.close).props('flat color=grey')
            ui.button('保存', on_click=save_new_group).classes('bg-blue-600 text-white')
    d.open()


# ================= 9. 页面渲染核心函数 =================

async def render_probe_page():
    state.CURRENT_VIEW_STATE['scope'] = 'PROBE'
    content_container.clear();
    content_container.classes(
        replace='w-full h-full overflow-y-auto p-6 bg-slate-50 relative flex flex-col justify-center items-center')
    with content_container:
        with ui.column().classes('w-full max-w-7xl gap-6'):
            with ui.row().classes('w-full items-center gap-3'):
                with ui.element('div').classes('p-2 bg-blue-600 rounded-lg shadow-sm'): ui.icon('tune',
                                                                                                color='white').classes(
                    'text-2xl')
                with ui.column().classes('gap-0'):
                    ui.label('探针管理与设置').classes('text-2xl font-extrabold text-slate-800 tracking-tight')
                    ui.label('Probe Configuration & Management').classes(
                        'text-xs font-bold text-gray-400 uppercase tracking-widest')

            with ui.grid().classes('w-full grid-cols-1 lg:grid-cols-7 gap-6 items-stretch'):
                with ui.column().classes('lg:col-span-4 w-full gap-6'):
                    with ui.card().classes('w-full p-6 bg-white border border-gray-200 shadow-sm rounded-xl'):
                        ui.label('基础连接设置').classes('text-lg font-bold text-slate-700 mb-4 border-b pb-2')
                        url_input = ui.input('主控端地址', value=state.ADMIN_CONFIG.get('manager_base_url', '')).props(
                            'outlined dense').classes('w-full')

                        async def save_url():
                            state.ADMIN_CONFIG['manager_base_url'] = url_input.value.strip().rstrip('/')
                            await logic.save_admin_config()
                            safe_notify('保存成功', 'positive')

                        ui.button('保存连接设置', on_click=save_url).props('unelevated color=blue-7').classes(
                            'font-bold self-end mt-4')

                    with ui.card().classes('w-full p-6 bg-white border border-gray-200 shadow-sm rounded-xl'):
                        ui.label('三网测速目标').classes('text-lg font-bold text-slate-700 mb-4 border-b pb-2')
                        with ui.grid().classes('w-full grid-cols-1 sm:grid-cols-3 gap-4'):
                            ping_ct = ui.input('电信 IP', value=state.ADMIN_CONFIG.get('ping_target_ct', '')).props(
                                'outlined dense')
                            ping_cu = ui.input('联通 IP', value=state.ADMIN_CONFIG.get('ping_target_cu', '')).props(
                                'outlined dense')
                            ping_cm = ui.input('移动 IP', value=state.ADMIN_CONFIG.get('ping_target_cm', '')).props(
                                'outlined dense')

                        async def save_ping():
                            state.ADMIN_CONFIG['ping_target_ct'] = ping_ct.value
                            state.ADMIN_CONFIG['ping_target_cu'] = ping_cu.value
                            state.ADMIN_CONFIG['ping_target_cm'] = ping_cm.value
                            await logic.save_admin_config()
                            safe_notify('保存成功', 'positive')

                        ui.button('保存测速目标', on_click=save_ping).props('unelevated color=orange-7').classes(
                            'font-bold self-end mt-4')

                with ui.column().classes('lg:col-span-3 w-full gap-6 h-full'):
                    with ui.card().classes(
                            'w-full p-6 bg-white border border-gray-200 shadow-sm rounded-xl flex-shrink-0'):
                        ui.label('快捷操作').classes(
                            'text-lg font-bold text-slate-700 mb-4 border-l-4 border-blue-500 pl-2')
                        ui.button('复制安装命令', icon='content_copy', on_click=lambda: safe_copy_to_clipboard(
                            "curl -sL https://raw.githubusercontent.com/SIJULY/x-fusion-panel/main/x-install.sh | bash")).classes(
                            'w-full bg-blue-50 text-blue-700 border border-blue-200 shadow-sm hover:bg-blue-100 font-bold')
                        with ui.row().classes('w-full gap-2'):
                            ui.button('分组管理', icon='settings',
                                      on_click=lambda: open_unified_group_manager('manage')).classes(
                                'flex-1 bg-blue-50 text-blue-700 border border-blue-200 shadow-sm')
                            ui.button('排序', icon='sort', on_click=open_group_sort_dialog).classes(
                                'flex-1 bg-gray-50 text-gray-700 border border-gray-200 shadow-sm')
                        ui.button('更新所有探针', icon='system_update_alt',
                                  on_click=logic.batch_install_all_probes).classes(
                            'w-full bg-orange-50 text-orange-700 border border-orange-200 shadow-sm')


async def load_subs_view():
    state.CURRENT_VIEW_STATE['scope'] = 'SUBS';
    show_loading(content_container)
    try:
        origin = await ui.run_javascript('return window.location.origin', timeout=3.0)
    except:
        origin = ""
    content_container.clear()
    with content_container:
        ui.label('订阅管理').classes('text-2xl font-bold mb-4')
        with ui.row().classes('w-full mb-4 justify-end'):
            ui.button('新建订阅', icon='add', color='green', on_click=lambda: open_advanced_sub_editor(None))

        for sub in state.SUBS_CACHE:
            with ui.card().classes(
                    'w-full p-4 mb-3 shadow-sm hover:shadow-md transition border-l-4 border-blue-500 rounded-lg'):
                with ui.row().classes('justify-between w-full items-start'):
                    with ui.column().classes('gap-1'):
                        ui.label(sub.get('name', '未命名')).classes('font-bold text-lg text-slate-800')
                        ui.label(f"包含节点: {len(sub.get('nodes', []))}").classes('text-xs text-gray-400')
                    with ui.row().classes('gap-2'):
                        ui.button('管理', icon='tune', on_click=lambda _, s=sub: open_advanced_sub_editor(s)).props(
                            'unelevated dense size=sm color=blue-7')
                        ui.button('删除', icon='delete', color='red', on_click=lambda _, s=sub: state.SUBS_CACHE.remove(
                            s) or logic.save_subs() or load_subs_view()).props('flat dense size=sm')
                path = f"/sub/{sub['token']}";
                raw_url = f"{origin}{path}"
                with ui.row().classes(
                        'w-full items-center gap-2 bg-slate-100 p-2 rounded justify-between border border-slate-200 mt-2'):
                    ui.label(raw_url).classes('text-xs font-mono text-slate-600 truncate select-all flex-grow')
                    ui.button(icon='content_copy', on_click=lambda u=raw_url: safe_copy_to_clipboard(u)).props(
                        'flat dense round size=xs text-color=grey-7')


def prepare_map_data():
    try:
        city_points_map = {}
        flag_points_map = {}
        unique_deployed_countries = set()
        region_stats = {}
        active_regions_for_highlight = set()

        FLAG_TO_MAP_NAME = {
            '🇨🇳': 'China', '🇭🇰': 'China', '🇲🇴': 'China', '🇹🇼': 'China',
            '🇺🇸': 'United States', '🇨🇦': 'Canada', '🇲🇽': 'Mexico',
            '🇬🇧': 'United Kingdom', '🇩🇪': 'Germany', '🇫🇷': 'France', '🇳🇱': 'Netherlands',
            '🇷🇺': 'Russia', '🇯🇵': 'Japan', '🇰🇷': 'South Korea', '🇸🇬': 'Singapore',
            '🇮🇳': 'India', '🇦🇺': 'Australia', '🇧🇷': 'Brazil', '🇦🇷': 'Argentina',
            '🇹🇷': 'Turkey', '🇮🇹': 'Italy', '🇪🇸': 'Spain', '🇵🇹': 'Portugal',
            '🇨🇭': 'Switzerland', '🇸🇪': 'Sweden', '🇳🇴': 'Norway', '🇫🇮': 'Finland',
            '🇵🇱': 'Poland', '🇺🇦': 'Ukraine', '🇮🇪': 'Ireland', '🇦🇹': 'Austria',
            '🇧🇪': 'Belgium', '🇩🇰': 'Denmark', '🇨🇿': 'Czech Republic', '🇬🇷': 'Greece',
            '🇿🇦': 'South Africa', '🇪🇬': 'Egypt', '🇸🇦': 'Saudi Arabia', '🇦🇪': 'United Arab Emirates',
            '🇮🇱': 'Israel', '🇮🇷': 'Iran', '🇮🇩': 'Indonesia', '🇲🇾': 'Malaysia',
            '🇹🇭': 'Thailand', '🇻🇳': 'Vietnam', '🇵🇭': 'Philippines', '🇨🇱': 'Chile',
            '🇨🇴': 'Colombia', '🇵🇪': 'Peru'
        }

        MAP_NAME_ALIASES = {
            'United States': ['United States of America', 'USA'],
            'United Kingdom': ['United Kingdom', 'UK', 'Great Britain'],
            'China': ['People\'s Republic of China'],
            'Russia': ['Russian Federation'],
            'South Korea': ['Korea', 'Republic of Korea'],
            'Vietnam': ['Viet Nam']
        }

        COUNTRY_CENTROIDS = {
            'China': [104.19, 35.86], 'United States': [-95.71, 37.09], 'United Kingdom': [-3.43, 55.37],
            'Germany': [10.45, 51.16], 'France': [2.21, 46.22], 'Netherlands': [5.29, 52.13],
            'Russia': [105.31, 61.52], 'Canada': [-106.34, 56.13], 'Brazil': [-51.92, -14.23],
            'Australia': [133.77, -25.27], 'India': [78.96, 20.59], 'Japan': [138.25, 36.20],
            'South Korea': [127.76, 35.90], 'Singapore': [103.81, 1.35], 'Turkey': [35.24, 38.96]
        }

        CITY_COORDS_FIX = {
            'Dubai': (25.20, 55.27), 'Frankfurt': (50.11, 8.68), 'Amsterdam': (52.36, 4.90),
            'San Jose': (37.33, -121.88), 'Phoenix': (33.44, -112.07), 'Tokyo': (35.68, 139.76),
            'Seoul': (37.56, 126.97), 'London': (51.50, -0.12), 'Singapore': (1.35, 103.81)
        }

        from collections import Counter
        country_counter = Counter()
        snapshot = list(state.SERVERS_CACHE)
        now_ts = time.time()

        temp_stats_storage = {}

        for s in snapshot:
            s_name = s.get('name', '')

            flag_icon = "📍"
            map_name_standard = None

            for f, m_name in FLAG_TO_MAP_NAME.items():
                if f in s_name:
                    flag_icon = f
                    map_name_standard = m_name
                    break

            if not map_name_standard:
                try:
                    group_str = logic.detect_country_group(s_name, s)
                    if group_str:
                        flag_part = group_str.split(' ')[0]
                        if flag_part in FLAG_TO_MAP_NAME:
                            flag_icon = flag_part
                            map_name_standard = FLAG_TO_MAP_NAME[flag_part]
                except:
                    pass

            try:
                country_counter[logic.detect_country_group(s_name, s)] += 1
            except:
                pass

            lat, lon = None, None
            for city_key, (c_lat, c_lon) in CITY_COORDS_FIX.items():
                if city_key.lower() in s_name.lower(): lat, lon = c_lat, c_lon; break
            if not lat:
                if 'lat' in s and 'lon' in s:
                    lat, lon = s['lat'], s['lon']
                else:
                    coords = utils.get_coords_from_name(s_name)
                    if coords: lat, lon = coords[0], coords[1]

            if lat and lon and map_name_standard:
                coord_key = f"{lat},{lon}"
                if coord_key not in city_points_map:
                    city_points_map[coord_key] = {'name': s_name, 'value': [lon, lat], 'country_key': map_name_standard}

                if flag_icon != "📍" and flag_icon not in flag_points_map:
                    flag_points_map[flag_icon] = {'name': flag_icon, 'value': [lon, lat],
                                                  'country_key': map_name_standard}

            if map_name_standard:
                unique_deployed_countries.add(map_name_standard)

                if map_name_standard not in temp_stats_storage:
                    cn_name = map_name_standard
                    try:
                        full_g = logic.detect_country_group(s_name, s)
                        if full_g and ' ' in full_g: cn_name = full_g.split(' ')[1]
                    except:
                        pass

                    temp_stats_storage[map_name_standard] = {
                        'flag': flag_icon, 'cn': cn_name,
                        'total': 0, 'online': 0, 'servers': []
                    }

                rs = temp_stats_storage[map_name_standard]
                rs['total'] += 1

                is_on = False
                probe_cache = state.PROBE_DATA_CACHE.get(s['url'])
                if probe_cache:
                    if now_ts - probe_cache.get('last_updated', 0) < 20:
                        is_on = True

                if not is_on and s.get('_status') == 'online':
                    is_on = True

                if is_on: rs['online'] += 1

                rs['servers'].append({
                    'name': s_name,
                    'status': 'online' if is_on else 'offline'
                })

                if map_name_standard not in COUNTRY_CENTROIDS and lat and lon:
                    COUNTRY_CENTROIDS[map_name_standard] = [lon, lat]

        for std_name, stats in temp_stats_storage.items():
            stats['servers'].sort(key=lambda x: 0 if x['status'] == 'online' else 1)
            region_stats[std_name] = stats
            active_regions_for_highlight.add(std_name)

            if std_name in MAP_NAME_ALIASES:
                for alias in MAP_NAME_ALIASES[std_name]:
                    region_stats[alias] = stats
                    active_regions_for_highlight.add(alias)

        pie_data = []
        if country_counter:
            sorted_counts = country_counter.most_common(5)
            for k, v in sorted_counts: pie_data.append({'name': f"{k} ({v})", 'value': v})
            others = sum(country_counter.values()) - sum(x[1] for x in sorted_counts)
            if others > 0: pie_data.append({'name': f"🏳️ 其他 ({others})", 'value': others})
        else:
            pie_data.append({'name': '暂无数据', 'value': 0})

        city_list = list(city_points_map.values())
        flag_list = list(flag_points_map.values())

        return (
            json.dumps({'cities': city_list, 'flags': flag_list, 'regions': list(active_regions_for_highlight)},
                       ensure_ascii=False),
            pie_data,
            len(unique_deployed_countries),
            json.dumps(region_stats, ensure_ascii=False),
            json.dumps(COUNTRY_CENTROIDS, ensure_ascii=False)
        )
    except Exception as e:
        print(f"[ERROR] prepare_map_data failed: {e}")
        return (json.dumps({'cities': [], 'flags': [], 'regions': []}), [], 0, "{}", "{}")


async def refresh_dashboard_ui():
    try:
        if not state.DASHBOARD_REFS.get('servers'): return

        data = logic.calculate_dashboard_data()
        if not data: return

        state.DASHBOARD_REFS['servers'].set_text(data['servers'])
        state.DASHBOARD_REFS['nodes'].set_text(data['nodes'])
        state.DASHBOARD_REFS['traffic'].set_text(data['traffic'])
        state.DASHBOARD_REFS['subs'].set_text(data['subs'])

        if state.DASHBOARD_REFS.get('bar_chart'):
            state.DASHBOARD_REFS['bar_chart'].options['xAxis']['data'] = data['bar_chart']['names']
            state.DASHBOARD_REFS['bar_chart'].options['series'][0]['data'] = data['bar_chart']['values']
            state.DASHBOARD_REFS['bar_chart'].update()

        if state.DASHBOARD_REFS.get('pie_chart'):
            state.DASHBOARD_REFS['pie_chart'].options['series'][0]['data'] = data['pie_chart']
            state.DASHBOARD_REFS['pie_chart'].update()

        globe_data_list = []
        seen_locations = set()
        for s in state.SERVERS_CACHE:
            lat, lon = None, None
            if 'lat' in s and 'lon' in s:
                lat, lon = s['lat'], s['lon']
            else:
                coords = utils.get_coords_from_name(s.get('name', ''))
                if coords: lat, lon = coords[0], coords[1]

            if lat is not None and lon is not None:
                coord_key = (round(lat, 2), round(lon, 2))
                if coord_key not in seen_locations:
                    seen_locations.add(coord_key)
                    flag_only = "📍"
                    try:
                        full_group = logic.detect_country_group(s.get('name', ''), s)
                        flag_only = full_group.split(' ')[0]
                    except:
                        pass
                    globe_data_list.append({'lat': lat, 'lon': lon, 'name': flag_only})

        if state.CURRENT_VIEW_STATE.get('scope') == 'DASHBOARD':
            import json
            json_data = json.dumps(globe_data_list, ensure_ascii=False)
            ui.run_javascript(f'if(window.updateDashboardMap) window.updateDashboardMap({json_data});')

    except Exception as e:
        pass


async def load_dashboard_stats():
    state.CURRENT_VIEW_STATE['scope'] = 'DASHBOARD'
    state.CURRENT_VIEW_STATE['data'] = None

    await asyncio.sleep(0.1)
    content_container.clear()
    content_container.classes(remove='justify-center items-center overflow-hidden p-6',
                              add='overflow-y-auto p-4 pl-6 justify-start')

    init_data = logic.calculate_dashboard_data()
    if not init_data:
        init_data = {
            "servers": "0/0", "nodes": "0", "traffic": "0 GB", "subs": "0",
            "bar_chart": {"names": [], "values": []}, "pie_chart": []
        }

    try:
        chart_data, pie_data, region_count, region_stats_json, centroids_json = prepare_map_data()
    except:
        chart_data = '{"cities": [], "flags": [], "regions": []}'
        pie_data = []

    with content_container:
        ui.label('系统概览').classes('text-3xl font-bold mb-4 text-slate-800 tracking-tight')

        with ui.row().classes('w-full gap-4 mb-6 items-stretch'):
            def create_stat_card(ref_key, title, sub_text, icon, gradient, init_val):
                with ui.card().classes(
                        f'flex-1 p-3 shadow border-none text-white {gradient} rounded-xl relative overflow-hidden'):
                    ui.element('div').classes('absolute -right-4 -top-4 w-20 h-20 bg-white opacity-10 rounded-full')
                    with ui.row().classes('items-center justify-between w-full relative z-10'):
                        with ui.column().classes('gap-0'):
                            ui.label(title).classes('opacity-90 text-[10px] font-bold uppercase tracking-wider')
                            state.DASHBOARD_REFS[ref_key] = ui.label(init_val).classes(
                                'text-2xl font-extrabold tracking-tight my-0.5')
                            ui.label(sub_text).classes('opacity-70 text-[10px] font-medium')
                        ui.icon(icon).classes('text-3xl opacity-80')

            create_stat_card('servers', '在线服务器', 'Online / Total', 'dns',
                             'bg-gradient-to-br from-blue-500 to-indigo-600', init_data['servers'])
            create_stat_card('nodes', '节点总数', 'Active Nodes', 'hub',
                             'bg-gradient-to-br from-purple-500 to-pink-600', init_data['nodes'])
            create_stat_card('traffic', '总流量消耗', 'Upload + Download', 'bolt',
                             'bg-gradient-to-br from-emerald-500 to-teal-600', init_data['traffic'])
            create_stat_card('subs', '订阅配置', 'Subscriptions', 'rss_feed',
                             'bg-gradient-to-br from-orange-400 to-red-500', init_data['subs'])

        with ui.row().classes('w-full gap-4 mb-6 flex-wrap xl:flex-nowrap items-stretch'):
            with ui.card().classes('w-full xl:w-2/3 p-4 shadow-md border-none rounded-xl bg-white flex flex-col'):
                with ui.row().classes('w-full justify-between items-center mb-2'):
                    ui.label('📊 服务器流量排行 (GB)').classes('text-base font-bold text-slate-700')
                    with ui.row().classes(
                            'items-center gap-1 px-2 py-0.5 bg-green-50 rounded-full border border-green-200'):
                        ui.element('div').classes('w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse')
                        ui.label('Live').classes('text-[10px] font-bold text-green-700')

                state.DASHBOARD_REFS['bar_chart'] = ui.echart({
                    'tooltip': {'trigger': 'axis'},
                    'grid': {'left': '2%', 'right': '3%', 'bottom': '2%', 'top': '10%', 'containLabel': True},
                    'xAxis': {'type': 'category', 'data': init_data['bar_chart']['names'],
                              'axisLabel': {'interval': 0, 'rotate': 30, 'color': '#64748b', 'fontSize': 10}},
                    'yAxis': {'type': 'value', 'splitLine': {'lineStyle': {'type': 'dashed', 'color': '#f1f5f9'}}},
                    'series': [{'type': 'bar', 'data': init_data['bar_chart']['values'], 'barWidth': '40%',
                                'itemStyle': {'borderRadius': [3, 3, 0, 0], 'color': '#6366f1'}}]
                }).classes('w-full h-56')

            with ui.card().classes('w-full xl:w-1/3 p-4 shadow-md border-none rounded-xl bg-white flex flex-col'):
                ui.label('🌏 服务器分布').classes('text-base font-bold text-slate-700 mb-1')
                color_palette = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#6366f1', '#ec4899', '#14b8a6',
                                 '#f97316']

                state.DASHBOARD_REFS['pie_chart'] = ui.echart({
                    'tooltip': {'trigger': 'item', 'formatter': '{b}: <br/><b>{c} 台</b> ({d}%)'},
                    'legend': {'bottom': '0%', 'left': 'center', 'icon': 'circle', 'itemGap': 10,
                               'textStyle': {'color': '#64748b', 'fontSize': 11}},
                    'color': color_palette,
                    'series': [{
                        'name': '服务器分布',
                        'type': 'pie',
                        'radius': ['40%', '70%'],
                        'center': ['50%', '42%'],
                        'avoidLabelOverlap': False,
                        'itemStyle': {'borderRadius': 4, 'borderColor': '#fff', 'borderWidth': 1},
                        'label': {'show': False, 'position': 'center'},
                        'emphasis': {'label': {'show': True, 'fontSize': 14, 'fontWeight': 'bold', 'color': '#334155'},
                                     'scale': True, 'scaleSize': 5},
                        'labelLine': {'show': False},
                        'data': pie_data
                    }]
                }).classes('w-full h-56')

        with ui.row().classes('w-full gap-6 mb-6'):
            with ui.card().classes('w-full p-0 shadow-md border-none rounded-xl bg-slate-900 overflow-hidden relative'):
                with ui.row().classes(
                        'w-full px-6 py-3 bg-slate-800/50 border-b border-gray-700 justify-between items-center z-10 relative'):
                    with ui.row().classes('gap-2 items-center'):
                        ui.icon('public', color='blue-4').classes('text-xl')
                        ui.label('全球节点实景 (Global View)').classes('text-base font-bold text-white')
                    state.DASHBOARD_REFS['map_info'] = ui.label('Live Rendering').classes('text-[10px] text-gray-400')

                ui.html(config.GLOBE_STRUCTURE, sanitize=False).classes('w-full h-[650px] overflow-hidden')

                ui.run_javascript(f'window.DASHBOARD_DATA = {chart_data};')
                ui.run_javascript(config.GLOBE_JS_LOGIC)


# ================= 5. 单机视图 (修复版：增加 xterm.js 依赖) =================
async def render_single_server_view(server_conf, force_refresh=False):
    # ✨✨✨ 新增：注入 xterm.js 终端库 ✨✨✨
    ui.add_head_html('''
        <link rel="stylesheet" href="/static/xterm.css" />
        <script src="/static/xterm.js"></script>
        <script src="/static/xterm-addon-fit.js"></script>
    ''')

    if content_container:
        content_container.clear()
        content_container.classes(remove='overflow-y-auto block', add='h-full overflow-hidden flex flex-col p-4')

    with content_container:
        # ... (后续代码保持完全一致) ...
        has_manager_access = (server_conf.get('url') and server_conf.get('user') and server_conf.get('pass')) or \
                             (server_conf.get('probe_installed') and server_conf.get('ssh_host'))

        mgr = None
        if has_manager_access:
            try:
                mgr = logic.get_manager(server_conf)
            except:
                pass

        SINGLE_COLS_NO_PING = 'grid-template-columns: 3fr 1fr 1.5fr 1fr 1fr 1fr 1.5fr; align-items: center;'
        btn_3d_base = 'text-xs font-bold text-white rounded-lg px-4 py-2 border-b-4 active:border-b-0 active:translate-y-[4px] transition-all duration-150 shadow-sm'
        btn_blue = f'bg-blue-600 border-blue-800 hover:bg-blue-500 {btn_3d_base}'
        btn_green = f'bg-green-600 border-green-800 hover:bg-green-500 {btn_3d_base}'

        async def reload_and_refresh_ui():
            if mgr and hasattr(mgr, '_exec_remote_script'):
                try:
                    new_inbounds = await logic.run_in_bg_executor(
                        lambda: asyncio.run(mgr.get_inbounds())) if not asyncio.iscoroutinefunction(
                        mgr.get_inbounds) else await mgr.get_inbounds()

                    if new_inbounds is not None:
                        state.NODES_DATA[server_conf['url']] = new_inbounds
                        server_conf['_status'] = 'online'
                        await logic.save_nodes_cache()
                except Exception as e:
                    pass
            else:
                try:
                    await logic.fetch_inbounds_safe(server_conf, force_refresh=True)
                except:
                    pass
            render_node_list.refresh()

        def open_edit_custom_node(node_data):
            with ui.dialog() as d, ui.card().classes('w-96 p-4'):
                ui.label('编辑节点备注').classes('text-lg font-bold mb-4')
                name_input = ui.input('节点名称', value=node_data.get('remark', '')).classes('w-full')

                async def save():
                    node_data['remark'] = name_input.value.strip()
                    await logic.save_servers()
                    safe_notify('修改已保存', 'positive')
                    d.close()
                    render_node_list.refresh()

                with ui.row().classes('w-full justify-end mt-4'):
                    ui.button('取消', on_click=d.close).props('flat')
                    ui.button('保存', on_click=save).classes('bg-blue-600 text-white')
            d.open()

        async def uninstall_and_delete(node_data):
            with ui.dialog() as d, ui.card().classes('w-96 p-6'):
                with ui.row().classes('items-center gap-2 text-red-600 mb-2'):
                    ui.icon('delete_forever', size='md');
                    ui.label('卸载并清理环境').classes('font-bold text-lg')
                ui.label(f"节点: {node_data.get('remark')}").classes('text-sm font-bold text-gray-800')
                ui.label("即将执行以下操作：").classes('text-xs text-gray-500 mt-2')

                domain_to_del = None
                raw_link = node_data.get('_raw_link', '')
                if raw_link and '://' in raw_link:
                    try:
                        from urllib.parse import urlparse, parse_qs
                        query = urlparse(raw_link).query;
                        params = parse_qs(query)
                        if 'sni' in params:
                            domain_to_del = params['sni'][0]
                        elif 'host' in params:
                            domain_to_del = params['host'][0]
                    except:
                        pass

                with ui.column().classes('ml-2 gap-1 mt-1'):
                    ui.label('1. 停止 Xray 服务并清除残留进程').classes('text-xs text-gray-600')
                    ui.label('2. 删除 Xray 配置文件').classes('text-xs text-gray-600')
                    if domain_to_del and state.ADMIN_CONFIG.get('cf_root_domain') in domain_to_del:
                        ui.label(f'3. 🗑️ 自动删除 CF 解析: {domain_to_del}').classes('text-xs text-red-500 font-bold')
                    else:
                        ui.label('3. 跳过 DNS 清理 (非托管域名)').classes('text-xs text-gray-400')

                async def start_uninstall():
                    d.close();
                    notification = ui.notification(message='正在执行卸载与清理...', timeout=0, spinner=True)
                    if domain_to_del:
                        cf = utils.CloudflareHandler()
                        if cf.token and cf.root_domain and (cf.root_domain in domain_to_del):
                            ok, msg = await cf.delete_record_by_domain(domain_to_del)
                            if ok:
                                safe_notify(f"☁️ {msg}", "positive")
                            else:
                                safe_notify(f"⚠️ DNS 删除失败: {msg}", "warning")
                    success, output = await logic.run_in_bg_executor(
                        lambda: utils._ssh_exec_wrapper(server_conf, config.XHTTP_UNINSTALL_SCRIPT))
                    notification.dismiss()
                    if success:
                        safe_notify('✅ 服务已卸载，进程已清理', 'positive')
                    else:
                        safe_notify(f'⚠️ SSH 卸载可能有残留: {output}', 'warning')

                    if 'custom_nodes' in server_conf and node_data in server_conf['custom_nodes']:
                        server_conf['custom_nodes'].remove(node_data)
                        await logic.save_servers()
                    await reload_and_refresh_ui()

                with ui.row().classes('w-full justify-end mt-6 gap-2'):
                    ui.button('取消', on_click=d.close).props('flat color=grey')
                    ui.button('确认执行', color='red', on_click=start_uninstall).props('unelevated')
            d.open()

        with ui.row().classes(
                'w-full justify-between items-center bg-white p-4 rounded-xl border border-gray-200 border-b-[4px] border-b-gray-300 shadow-sm flex-shrink-0'):
            with ui.row().classes('items-center gap-4'):
                sys_icon = 'computer' if 'Oracle' in server_conf.get('name', '') else 'dns'
                with ui.element('div').classes('p-3 bg-slate-100 rounded-lg border border-slate-200'):
                    ui.icon(sys_icon, size='md').classes('text-slate-700')
                with ui.column().classes('gap-1'):
                    ui.label(server_conf.get('name', '未命名服务器')).classes(
                        'text-xl font-black text-slate-800 leading-tight tracking-tight')
                    with ui.row().classes('items-center gap-2'):
                        ip_addr = server_conf.get('ssh_host') or \
                                  server_conf.get('url', '').replace('http://', '').split(':')[0]
                        ui.label(ip_addr).classes(
                            'text-xs font-mono font-bold text-slate-500 bg-slate-100 px-2 py-0.5 rounded')
                        if server_conf.get('_status') == 'online':
                            ui.badge('Online', color='green').props('rounded outline size=xs')
                        else:
                            ui.badge('Offline', color='grey').props('rounded outline size=xs')

            with ui.row().classes('gap-3'):
                ui.button('一键部署 XHTTP', icon='rocket_launch',
                          on_click=lambda: open_deploy_xhttp_dialog(server_conf, reload_and_refresh_ui)).props(
                    'unelevated').classes(btn_blue)
                ui.button('一键部署 Hy2', icon='bolt',
                          on_click=lambda: open_deploy_hysteria_dialog(server_conf, reload_and_refresh_ui)).props(
                    'unelevated').classes(btn_blue)

                if has_manager_access:
                    async def on_add_success():
                        ui.notify('添加节点成功');
                        await reload_and_refresh_ui()

                    ui.button('新建 XUI 节点', icon='add',
                              on_click=lambda: open_inbound_dialog(mgr, None, on_add_success)).props(
                        'unelevated').classes(btn_green)
                else:
                    ui.button('探针只读', icon='visibility', on_click=None).props('unelevated disabled').classes(
                        'bg-gray-400 border-gray-600 text-white rounded-lg px-4 py-2 border-b-4 text-xs font-bold opacity-70')

        ui.element('div').classes('h-4 flex-shrink-0')

        with ui.card().classes(
                'w-full flex-grow flex flex-col p-0 rounded-xl border border-gray-200 border-b-[4px] border-b-gray-300 shadow-sm overflow-hidden'):
            with ui.row().classes('w-full items-center justify-between p-3 bg-gray-50 border-b border-gray-200'):
                ui.label('节点列表').classes('text-sm font-black text-gray-600 uppercase tracking-wide ml-1')
                if server_conf.get('probe_installed') and server_conf.get('ssh_host'):
                    ui.badge('Root 模式', color='teal').props('outline rounded size=xs')
                elif server_conf.get('user'):
                    ui.badge('API 托管模式', color='blue').props('outline rounded size=xs')

            with ui.element('div').classes(
                    'grid w-full gap-4 font-bold text-gray-400 border-b border-gray-200 pb-2 pt-2 px-2 text-xs uppercase tracking-wider bg-white').style(
                    SINGLE_COLS_NO_PING):
                ui.label('节点名称').classes('text-left pl-2')
                for h in ['类型', '流量', '协议', '端口', '状态', '操作']: ui.label(h).classes('text-center')

            @ui.refreshable
            async def render_node_list():
                xui_nodes = await logic.fetch_inbounds_safe(server_conf, force_refresh=False)
                if xui_nodes is None: xui_nodes = []

                custom_nodes = server_conf.get('custom_nodes', [])
                all_nodes = xui_nodes + custom_nodes

                if not all_nodes:
                    with ui.column().classes('w-full py-12 items-center justify-center opacity-50'):
                        msg = "暂无节点 (可直接新建)" if has_manager_access else "暂无数据"
                        ui.icon('inbox', size='4rem').classes('text-gray-300 mb-2')
                        ui.label(msg).classes('text-gray-400 text-sm')
                else:
                    for n in all_nodes:
                        is_custom = n.get('_is_custom', False)
                        is_ssh_mode = (not is_custom) and (
                                    server_conf.get('probe_installed') and server_conf.get('ssh_host'))
                        row_3d_cls = 'grid w-full gap-4 py-3 px-2 mb-2 items-center group bg-white rounded-xl border border-gray-200 border-b-[3px] shadow-sm transition-all duration-150 ease-out hover:shadow-md hover:border-blue-300 hover:-translate-y-[2px] active:border-b active:translate-y-[2px] active:shadow-none cursor-default'

                        with ui.element('div').classes(row_3d_cls).style(SINGLE_COLS_NO_PING):
                            ui.label(n.get('remark', '未命名')).classes(
                                'font-bold truncate w-full text-left pl-2 text-slate-700 text-sm')

                            if is_custom:
                                ui.label("独立").classes(
                                    'text-[10px] bg-purple-100 text-purple-700 font-bold px-2 py-0.5 rounded-full w-fit mx-auto shadow-sm')
                            elif is_ssh_mode:
                                ui.label("Root").classes(
                                    'text-[10px] bg-teal-100 text-teal-700 font-bold px-2 py-0.5 rounded-full w-fit mx-auto shadow-sm')
                            else:
                                ui.label("API").classes(
                                    'text-[10px] bg-gray-100 text-gray-600 font-bold px-2 py-0.5 rounded-full w-fit mx-auto shadow-sm')

                            traffic = utils.format_bytes(n.get('up', 0) + n.get('down', 0)) if not is_custom else "--"
                            ui.label(traffic).classes('text-xs text-gray-500 w-full text-center font-mono font-bold')

                            proto = str(n.get('protocol', 'unk')).upper()
                            ui.label(proto).classes(
                                f'text-[11px] font-extrabold w-full text-center text-slate-500 tracking-wide')

                            ui.label(str(n.get('port', 0))).classes(
                                'text-blue-600 font-mono w-full text-center font-bold text-xs')

                            is_enable = n.get('enable', True)
                            with ui.row().classes('w-full justify-center items-center gap-1'):
                                color = "green" if is_enable else "red";
                                text = "启用" if is_enable else "停止"
                                ui.element('div').classes(
                                    f'w-2 h-2 rounded-full bg-{color}-500 shadow-[0_0_5px_rgba(0,0,0,0.2)]')
                                ui.label(text).classes(f'text-[10px] font-bold text-{color}-600')

                            with ui.row().classes(
                                    'gap-2 justify-center w-full no-wrap opacity-60 group-hover:opacity-100 transition'):
                                btn_props = 'flat dense size=sm round'
                                link = n.get('_raw_link', '') if is_custom else utils.generate_node_link(n, server_conf[
                                    'url'])
                                if link: ui.button(icon='content_copy',
                                                   on_click=lambda u=link: safe_copy_to_clipboard(u)).props(
                                    btn_props).tooltip('复制链接').classes(
                                    'text-gray-600 hover:bg-blue-50 hover:text-blue-600')

                                async def copy_detail_action(node_item=n):
                                    host = \
                                    server_conf.get('url', '').replace('http://', '').replace('https://', '').split(
                                        ':')[0]
                                    text = utils.generate_detail_config(node_item, host)
                                    if text:
                                        await safe_copy_to_clipboard(text)
                                    else:
                                        ui.notify('该协议不支持生成明文配置', type='warning')

                                ui.button(icon='description', on_click=copy_detail_action).props(btn_props).tooltip(
                                    '复制明文配置').classes('text-gray-600 hover:bg-orange-50 hover:text-orange-600')

                                if is_custom:
                                    ui.button(icon='edit', on_click=lambda node=n: open_edit_custom_node(node)).props(
                                        btn_props).classes('text-blue-600 hover:bg-blue-50')
                                    ui.button(icon='delete', on_click=lambda node=n: uninstall_and_delete(node)).props(
                                        btn_props).classes('text-red-500 hover:bg-red-50')
                                elif has_manager_access:
                                    async def on_edit_success():
                                        ui.notify('修改成功');
                                        await reload_and_refresh_ui()

                                    ui.button(icon='edit',
                                              on_click=lambda i=n: open_inbound_dialog(mgr, i, on_edit_success)).props(
                                        btn_props).classes('text-blue-600 hover:bg-blue-50')

                                    async def on_del_success():
                                        ui.notify('删除成功');
                                        await reload_and_refresh_ui()

                                    ui.button(icon='delete',
                                              on_click=lambda i=n: delete_inbound_with_confirm(mgr, i['id'],
                                                                                               i.get('remark', ''),
                                                                                               on_del_success)).props(
                                        btn_props).classes('text-red-500 hover:bg-red-50')
                                else:
                                    ui.icon('lock', size='xs').classes('text-gray-300').tooltip('无权限')

            with ui.scroll_area().classes('w-full flex-grow bg-gray-50 p-1'):
                await render_node_list()

        ui.element('div').classes('h-6 flex-shrink-0')

        with ui.card().classes(
                'w-full h-[750px] flex-shrink-0 p-0 rounded-xl border border-gray-300 border-b-[4px] border-b-gray-400 shadow-lg overflow-hidden bg-slate-900 flex flex-col'):
            box = ui.element('div').classes('w-full h-full')
            ssh_inst = WebSSH(box, server_conf)
            await ssh_inst.connect()


async def on_server_click_handler(server):
    if state.CURRENT_VIEW_STATE.get('scope') == 'SINGLE' and state.CURRENT_VIEW_STATE.get('data', {}).get(
            'url') == server.get('url'):
        return
    await refresh_content('SINGLE', server)


def render_single_sidebar_row(s):
    with ui.row().classes('w-full gap-2 no-wrap items-stretch') as row:
        ui.button(on_click=lambda _, s=s: on_server_click_handler(s)).bind_text_from(s, 'name').props(
            'no-caps align=left flat text-color=grey-8').classes(
            'flex-grow text-xs font-bold text-gray-700 truncate px-3 py-2.5 bg-white border border-gray-200 rounded-lg')
        ui.button(icon='settings', on_click=lambda _, s=s: open_server_dialog(state.SERVERS_CACHE.index(s))).props(
            'flat square size=sm').classes('w-10 bg-white border border-gray-200 rounded-lg')
    state.SIDEBAR_UI_REFS['rows'][s['url']] = row
    return row


@ui.refreshable
def render_sidebar_content():
    state.SIDEBAR_UI_REFS['groups'].clear();
    state.SIDEBAR_UI_REFS['rows'].clear()

    # 1. 顶部 Logo 与主导航
    with ui.column().classes('w-full p-4 border-b bg-gray-50 flex-shrink-0'):
        ui.label('X-Fusion').classes('text-2xl font-black mb-4')
        for l, i, f in [('仪表盘', 'dashboard', load_dashboard_stats), ('探针', 'tune', render_probe_page),
                        ('订阅', 'rss_feed', load_subs_view)]:
            ui.button(l, icon=i, on_click=f).props('flat align=left').classes('w-full bg-white border rounded-lg')

    # 2. 服务器列表区域
    with ui.column().classes('w-full flex-grow overflow-y-auto p-2 gap-2 bg-slate-50'):

        # ✨✨✨ 新增：操作按钮区 ✨✨✨
        with ui.row().classes('w-full gap-2 px-1 mb-1'):
            ui.button('添加服务器', icon='add', on_click=lambda: open_server_dialog(None)).props(
                'unelevated dense').classes('flex-grow bg-blue-600 text-white font-bold text-xs shadow-sm')
            ui.button('新建分组', icon='create_new_folder', on_click=open_quick_group_create_dialog).props(
                'outline dense').classes('flex-grow text-blue-600 border-blue-600 font-bold text-xs bg-white shadow-sm')

        # 所有服务器入口
        with ui.row().classes(
                'w-full items-center p-3 border rounded-xl bg-white shadow-sm cursor-pointer hover:bg-gray-50 transition').on(
                'click', lambda: refresh_content('ALL')):
            ui.label('所有服务器').classes('font-bold text-slate-700')
            ui.badge(str(len(state.SERVERS_CACHE))).props('color=blue-600 text-color=white')

        # 自定义分组渲染
        tags = state.ADMIN_CONFIG.get('custom_groups', [])
        if tags:
            ui.label('自定义分组').classes('text-xs font-bold text-gray-400 mt-2 px-2 uppercase')
            for t in tags:
                srvs = [s for s in state.SERVERS_CACHE if t in s.get('tags', []) or s.get('group') == t]
                with ui.expansion(t, icon='folder').classes('w-full border rounded-xl bg-white mb-1 shadow-sm').props(
                        'header-class="font-bold text-slate-700"'):
                    with ui.column().classes('w-full gap-2 p-2 bg-gray-50/50 border-t') as col:
                        state.SIDEBAR_UI_REFS['groups'][t] = col
                        for s in srvs: render_single_sidebar_row(s)

        # 区域分组渲染
        ui.label('区域分组').classes('text-xs font-bold text-gray-400 mt-2 px-2 uppercase')
        buckets = {}
        for s in state.SERVERS_CACHE:
            g = logic.detect_country_group(s.get('name', ''), s) or '🏳️ 其他'
            if g not in buckets: buckets[g] = []
            buckets[g].append(s)

        # 按名称排序分组
        for g in sorted(buckets.keys()):
            srvs = buckets[g]
            with ui.expansion(g).classes('w-full border rounded-xl bg-white mb-1 shadow-sm'):
                with ui.column().classes('w-full gap-2 p-2 bg-slate-50 border-t') as col:
                    state.SIDEBAR_UI_REFS['groups'][g] = col
                    for s in srvs: render_single_sidebar_row(s)

    with ui.column().classes(
            'w-full p-2 border-t mt-auto mb-4 gap-2 bg-white z-10 shadow-[0_-4px_6px_-1px_rgba(0,0,0,0.05)]'):
        bottom_btn_3d = 'w-full text-gray-600 text-xs font-bold bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 transition-all duration-200 hover:bg-white hover:shadow-md hover:border-slate-300 hover:text-slate-900 active:translate-y-[1px] active:bg-slate-100 active:shadow-none'
        ui.button('批量 SSH 执行', icon='playlist_play', on_click=batch_ssh_manager.open_dialog).props(
            'flat align=left').classes(bottom_btn_3d)
        ui.button('Cloudflare 设置', icon='cloud', on_click=open_cloudflare_settings_dialog).props(
            'flat align=left').classes(bottom_btn_3d)
        ui.button('全局 SSH 设置', icon='vpn_key', on_click=open_global_settings_dialog).props(
            'flat align=left').classes(bottom_btn_3d)
        ui.button('数据备份 / 恢复', icon='save', on_click=open_data_mgmt_dialog).props('flat align=left').classes(
            bottom_btn_3d)


# ================= 10. 内容刷新与路由 =================
async def refresh_content(scope='ALL', data=None, force_refresh=False, sync_name_action=False, page_num=1,
                          manual_client=None):
    client = manual_client
    if not client:
        try:
            client = ui.context.client
        except:
            pass
    if not client: return

    with client:
        import time
        cache_key = f"{scope}::{data}::P{page_num}";
        lock_key = cache_key
        now = time.time();
        last_sync = state.LAST_SYNC_MAP.get(cache_key, 0)

        targets = get_targets_by_scope(scope, data)
        PAGE_SIZE = 30
        start_idx = (page_num - 1) * PAGE_SIZE;
        end_idx = start_idx + PAGE_SIZE
        current_page_servers = targets[start_idx:end_idx] if targets else []

        has_probe = False;
        has_api_only = False
        for s in current_page_servers:
            if s.get('probe_installed', False):
                has_probe = True
            else:
                has_api_only = True

        SYNC_COOLDOWN = 1800

        if not force_refresh and (now - last_sync < SYNC_COOLDOWN):
            state.CURRENT_VIEW_STATE['scope'] = scope;
            state.CURRENT_VIEW_STATE['data'] = data;
            state.CURRENT_VIEW_STATE['page'] = page_num
            await _render_ui_internal(scope, data, page_num, force_refresh, sync_name_action, client)

            mins_ago = int((now - last_sync) / 60)
            notify_msg = "";
            notify_type = "ongoing"
            if not current_page_servers:
                notify_msg = "列表为空"
            elif has_probe and not has_api_only:
                notify_msg = "⚡ 实时数据 (探针秒级推送)"; notify_type = "positive"
            elif not has_probe and has_api_only:
                notify_msg = f"🕒 显示缓存数据 ({mins_ago}分钟前)"
            else:
                notify_msg = f"⚡ 探针实时 + 🕒 API缓存 ({mins_ago}分前)"
            safe_notify(notify_msg, notify_type, timeout=1500)
            return

        if lock_key in state.REFRESH_LOCKS:
            if force_refresh: safe_notify(f"第 {page_num} 页正在更新中...", type='warning')
            return

        state.CURRENT_VIEW_STATE['scope'] = scope;
        state.CURRENT_VIEW_STATE['data'] = data;
        state.CURRENT_VIEW_STATE['page'] = page_num
        await _render_ui_internal(scope, data, page_num, force_refresh, sync_name_action, client)

        if not current_page_servers: return
        state.REFRESH_LOCKS.add(lock_key)

        async def _background_fetch():
            try:
                with client:
                    real_sync_count = len([s for s in current_page_servers if not s.get('probe_installed', False)])
                    if real_sync_count > 0: safe_notify(f"正在同步 {real_sync_count} 个 API 节点...", "ongoing",
                                                        timeout=1000)
                    tasks = [logic.fetch_inbounds_safe(s, force_refresh=True, sync_name=sync_name_action) for s in
                             current_page_servers]
                    await asyncio.gather(*tasks, return_exceptions=True)
                    try:
                        render_sidebar_content.refresh()
                    except:
                        pass
                    await _render_ui_internal(scope, data, page_num, force_refresh, sync_name_action, client)
                    state.LAST_SYNC_MAP[cache_key] = time.time()
                    if real_sync_count > 0:
                        if force_refresh: safe_notify(f"同步完成", "positive")
            finally:
                state.REFRESH_LOCKS.discard(lock_key)

        asyncio.create_task(_background_fetch())


async def _render_ui_internal(scope, data, page_num, force_refresh, sync_name_action, client):
    global content_container
    if content_container:
        content_container.clear();
        content_container.classes(remove='justify-center items-center overflow-hidden p-6',
                                  add='overflow-y-auto p-4 pl-6 justify-start')
        with content_container:
            targets = get_targets_by_scope(scope, data)
            if scope == 'SINGLE':
                if targets:
                    await render_single_server_view(data); return
                else:
                    ui.label('服务器未找到'); return

            title = "";
            is_group_view = False;
            show_ping = False
            if scope == 'ALL':
                title = f"🌍 所有服务器 ({len(targets)})"
            elif scope == 'TAG':
                title = f"🏷️ 自定义分组: {data} ({len(targets)})"; is_group_view = True
            elif scope == 'COUNTRY':
                title = f"🏳️ 区域: {data} ({len(targets)})"; is_group_view = True; show_ping = True

            with ui.row().classes('items-center w-full mb-4 border-b pb-2 justify-between'):
                with ui.row().classes('items-center gap-4'):
                    ui.label(title).classes('text-2xl font-bold')
                with ui.row().classes('items-center gap-2'):
                    if is_group_view and targets:
                        with ui.row().classes('gap-1'):
                            ui.button(icon='content_copy',
                                      on_click=lambda: safe_copy_to_clipboard(f"Copy Group: {data}")).props(
                                'flat dense round size=sm color=grey')
                    if targets:
                        ui.button('同步当前页', icon='sync',
                                  on_click=lambda: refresh_content(scope, data, force_refresh=True,
                                                                   sync_name_action=True, page_num=page_num,
                                                                   manual_client=client)).props('outline color=primary')

            if not targets:
                with ui.column().classes('w-full h-64 justify-center items-center text-gray-400'):
                    ui.icon('inbox', size='4rem'); ui.label('列表为空')
            else:
                try:
                    targets.sort(key=lambda x: x.get('name', ''))
                except:
                    pass
                await render_aggregated_view(targets, show_ping=show_ping, token=None, initial_page=page_num)


def get_targets_by_scope(scope, data):
    targets = []
    try:
        if scope == 'ALL':
            targets = list(state.SERVERS_CACHE)
        elif scope == 'TAG':
            targets = [s for s in state.SERVERS_CACHE if data in s.get('tags', [])]
        elif scope == 'COUNTRY':
            for s in state.SERVERS_CACHE:
                saved = s.get('group')
                real = saved if saved and saved not in ['默认分组', '自动注册', '未分组', '自动导入',
                                                        '🏳️ 其他地区'] else logic.detect_country_group(
                    s.get('name', ''))
                if real == data: targets.append(s)
        elif scope == 'SINGLE':
            if data in state.SERVERS_CACHE: targets = [data]
    except:
        pass
    return targets


async def render_aggregated_view(server_list, show_ping=False, token=None, initial_page=1):
    parent_client = ui.context.client
    list_container = ui.column().classes('w-full gap-3 p-1')

    PAGE_SIZE = 30;
    total = len(server_list);
    pages = (total + PAGE_SIZE - 1) // PAGE_SIZE

    def render_page(p_num):
        list_container.clear();
        state.CURRENT_VIEW_STATE['page'] = p_num
        start = (p_num - 1) * PAGE_SIZE;
        end = start + PAGE_SIZE;
        curr = server_list[start:end]
        with list_container:
            ui.pagination(1, pages, value=p_num).on('update:model-value', lambda e: render_page(e.value))
            for srv in curr:
                draw_row(srv, None)

    def draw_row(srv, node):
        with ui.card().classes('w-full p-2'):
            ui.label(srv['name'])

    render_page(initial_page)


# ================= 11. 页面入口函数 =================

def login_page(request):
    # 容器：用于切换登录步骤 (账号密码 -> MFA)
    container = ui.card().classes('absolute-center w-full max-w-sm p-8 shadow-2xl rounded-xl bg-white')

    # --- 步骤 1: 账号密码验证 ---
    def render_step1():
        container.clear()
        with container:
            ui.label('X-Fusion Panel').classes('text-2xl font-extrabold mb-2 w-full text-center text-slate-800')
            ui.label('请登录以继续').classes('text-sm text-gray-400 mb-6 w-full text-center')

            username = ui.input('账号').props('outlined dense').classes('w-full mb-3')
            password = ui.input('密码', password=True).props('outlined dense').classes('w-full mb-6').on(
                'keydown.enter', lambda: check_cred())

            def check_cred():
                if username.value == config.ADMIN_USER and password.value == config.ADMIN_PASS:
                    # 账号密码正确，进入 MFA 流程
                    check_mfa()
                else:
                    ui.notify('账号或密码错误', color='negative', position='top')

            ui.button('下一步', on_click=check_cred).classes('w-full bg-slate-900 text-white shadow-lg h-10')

            ui.label('© Powered by X-Fusion').classes(
                'text-xs text-gray-400 mt-6 w-full text-center font-mono opacity-80')

    # --- 步骤 2: MFA 验证或设置 ---
    def check_mfa():
        secret = state.ADMIN_CONFIG.get('mfa_secret')
        if not secret:
            # 如果没有密钥，进入初始化流程 (生成新密钥)
            new_secret = pyotp.random_base32()
            render_setup(new_secret)
        else:
            # 已有密钥，进入验证流程
            render_verify(secret)

    # 渲染 MFA 设置页面 (首次登录)
    def render_setup(secret):
        container.clear()

        totp_uri = pyotp.totp.TOTP(secret).provisioning_uri(name=config.ADMIN_USER, issuer_name="X-Fusion Panel")
        qr = qrcode.make(totp_uri)
        img_buffer = io.BytesIO()
        qr.save(img_buffer, format='PNG')
        img_b64 = base64.b64encode(img_buffer.getvalue()).decode('utf-8')

        with container:
            ui.label('绑定二次验证 (MFA)').classes('text-xl font-bold mb-2 w-full text-center')
            ui.label('请使用 Authenticator App 扫描').classes('text-xs text-gray-400 mb-2 w-full text-center')

            with ui.row().classes('w-full justify-center mb-2'):
                ui.image(f'data:image/png;base64,{img_b64}').style('width: 180px; height: 180px')

            with ui.row().classes(
                    'w-full justify-center items-center gap-1 mb-4 bg-gray-100 p-1 rounded cursor-pointer').on('click',
                                                                                                               lambda: safe_copy_to_clipboard(
                                                                                                                       secret)):
                ui.label(secret).classes('text-xs font-mono text-gray-600')
                ui.icon('content_copy').classes('text-gray-400 text-xs')

            code = ui.input('验证码', placeholder='6位数字').props('outlined dense input-class=text-center').classes(
                'w-full mb-4')

            async def confirm():
                totp = pyotp.TOTP(secret)
                if totp.verify(code.value):
                    state.ADMIN_CONFIG['mfa_secret'] = secret
                    await logic.save_admin_config()
                    ui.notify('绑定成功', type='positive')
                    finish()
                else:
                    ui.notify('验证码错误', type='negative')

            ui.button('确认绑定', on_click=confirm).classes('w-full bg-green-600 text-white h-10')

    # 渲染 MFA 验证页面 (日常登录)
    def render_verify(secret):
        container.clear()
        with container:
            ui.label('安全验证').classes('text-xl font-bold mb-6 w-full text-center')
            with ui.column().classes('w-full items-center mb-6'):
                ui.icon('verified_user').classes('text-6xl text-blue-600 mb-2')
                ui.label('请输入 Authenticator 动态码').classes('text-xs text-gray-400')

            code = ui.input(placeholder='------').props(
                'outlined input-class=text-center text-xl tracking-widest').classes('w-full mb-6')
            code.on('keydown.enter', lambda: verify())
            ui.timer(0.1, lambda: ui.run_javascript(f'document.querySelector(".q-field__native").focus()'), once=True)

            def verify():
                totp = pyotp.TOTP(secret)
                if totp.verify(code.value):
                    finish()
                else:
                    ui.notify('无效的验证码', type='negative', position='top')
                    code.value = ''

            ui.button('验证登录', on_click=verify).classes('w-full bg-slate-900 text-white h-10')
            ui.button('返回', on_click=render_step1).props('flat dense').classes('w-full mt-2 text-gray-400 text-xs')

    def finish():
        # 1. 基础认证标记
        app.storage.user['authenticated'] = True

        # 2. 写入全局版本号 (防止被踢出)
        if 'session_version' not in state.ADMIN_CONFIG:
            state.ADMIN_CONFIG['session_version'] = str(uuid.uuid4())[:8]
        app.storage.user['session_version'] = state.ADMIN_CONFIG['session_version']

        # 3. 记录 IP
        try:
            client_ip = request.headers.get("X-Forwarded-For", request.client.host).split(',')[0].strip()
            app.storage.user['last_known_ip'] = client_ip
        except:
            pass

        ui.navigate.to('/')

    render_step1()


def main_page(request):
    # 1. 认证检查辅助函数
    def check_auth(request):
        if not app.storage.user.get('authenticated', False): return False
        current_global_ver = state.ADMIN_CONFIG.get('session_version', 'init')
        user_ver = app.storage.user.get('session_version', '')
        if current_global_ver != user_ver: return False
        return True

    if not check_auth(request):
        return ui.navigate.to('/login')

    # 2. IP 变动检测与处理
    try:
        current_ip = request.headers.get("X-Forwarded-For", request.client.host).split(',')[0].strip()
    except:
        current_ip = "Unknown"
    display_ip = current_ip
    last_ip = app.storage.user.get('last_known_ip', '')
    app.storage.user['last_known_ip'] = current_ip

    async def reset_global_session(dialog_ref=None):
        new_ver = str(uuid.uuid4())[:8]
        state.ADMIN_CONFIG['session_version'] = new_ver
        await logic.save_admin_config()
        if dialog_ref: dialog_ref.close()
        ui.notify('🔒 安全密钥已重置，正在强制所有设备下线...', type='warning', close_button=False)
        await asyncio.sleep(1.5)
        app.storage.user.clear()
        ui.navigate.to('/login')

    if last_ip and last_ip != current_ip:
        def open_ip_alert():
            with ui.dialog() as d, ui.card().classes('w-96 p-5 border-t-4 border-red-500 shadow-2xl'):
                with ui.row().classes('items-center gap-2 text-red-600 mb-2'):
                    ui.icon('security', size='md');
                    ui.label('安全警告：登录 IP 变动').classes('font-bold text-lg')
                ui.label('检测到您的登录 IP 发生了变化：').classes('text-sm text-gray-600')
                with ui.grid().classes('grid-cols-2 gap-2 my-4 bg-red-50 p-3 rounded border border-red-100'):
                    ui.label('上次 IP:').classes('text-xs font-bold text-gray-500')
                    ui.label(last_ip).classes('text-xs font-mono font-bold text-gray-800')
                    ui.label('本次 IP:').classes('text-xs font-bold text-gray-500')
                    ui.label(current_ip).classes('text-xs font-mono font-bold text-blue-600')
                ui.label('如果是您切换了网络 (如 Wi-Fi 转 4G)，请忽略。').classes('text-xs text-gray-400')
                ui.label('若非本人操作，请立即重置密钥！').classes('text-xs text-red-500 font-bold mt-1')
                with ui.row().classes('w-full justify-end gap-2 mt-4'):
                    ui.button('我知道了', on_click=d.close).props('flat dense color=grey')
                    ui.button('强制所有设备下线', color='red', icon='gpp_bad',
                              on_click=lambda: reset_global_session(d)).props('unelevated dense')
            d.open()

        ui.timer(0.5, open_ip_alert, once=True)

    # 3. UI 构建
    with ui.left_drawer(value=True, fixed=True).classes('bg-gray-50 border-r').props('width=400 bordered') as drawer:
        render_sidebar_content()

    with ui.header().classes('bg-slate-900 text-white h-14 shadow-md'):
        with ui.row().classes('w-full items-center justify-between'):
            with ui.row().classes('items-center gap-2'):
                ui.button(icon='menu', on_click=lambda: drawer.toggle()).props('flat round dense color=white')
                ui.label('X-Fusion Panel').classes('text-lg font-bold ml-2 tracking-wide')
                ui.label(f"[{display_ip}]").classes('text-xs text-gray-400 font-mono pt-1 hidden sm:block')

            with ui.row().classes('items-center gap-2 mr-2'):
                with ui.button(icon='gpp_bad', color='red', on_click=lambda: reset_global_session(None)).props(
                        'flat dense round').tooltip('安全重置：强制所有已登录用户下线'):
                    ui.badge('Reset', color='orange').props('floating rounded')
                with ui.button(icon='vpn_key',
                               on_click=lambda: safe_copy_to_clipboard(config.AUTO_REGISTER_SECRET)).props(
                        'flat dense round').tooltip('点击复制通讯密钥'):
                    ui.badge('Key', color='red').props('floating rounded')
                ui.button(icon='logout', on_click=lambda: (app.storage.user.clear(), ui.navigate.to('/login'))).props(
                    'flat round dense').tooltip('退出登录')

    global content_container
    content_container = ui.column().classes('w-full h-full pl-4 pr-4 pt-4 overflow-y-auto bg-slate-50')

    async def auto_init_system_settings():
        try:
            current_origin = await ui.run_javascript('return window.location.origin', timeout=3.0)
            if not current_origin: return
            stored_url = state.ADMIN_CONFIG.get('manager_base_url', '')
            need_save = False
            if 'session_version' not in state.ADMIN_CONFIG:
                state.ADMIN_CONFIG['session_version'] = 'init_v1';
                need_save = True
            if not stored_url or 'xui-manager' in stored_url or '127.0.0.1' in stored_url:
                state.ADMIN_CONFIG['manager_base_url'] = current_origin;
                need_save = True
            if not state.ADMIN_CONFIG.get('probe_enabled'):
                state.ADMIN_CONFIG['probe_enabled'] = True;
                need_save = True
            if need_save: await logic.save_admin_config()
        except:
            pass

    ui.timer(1.0, auto_init_system_settings, once=True)

    async def restore_last_view():
        last_scope = app.storage.user.get('last_view_scope', 'DASHBOARD')
        last_data_id = app.storage.user.get('last_view_data', None)
        target_data = last_data_id
        if last_scope == 'SINGLE' and last_data_id:
            target_data = next((s for s in state.SERVERS_CACHE if s['url'] == last_data_id), None)
            if not target_data: last_scope = 'DASHBOARD'

        if last_scope == 'DASHBOARD':
            await load_dashboard_stats()
        elif last_scope == 'PROBE':
            await render_probe_page()
        elif last_scope == 'SUBS':
            await load_subs_view()
        else:
            await refresh_content(last_scope, target_data)

    ui.timer(0.1, lambda: asyncio.create_task(restore_last_view()), once=True)


async def status_page_router(request):
    def is_mobile(req):
        ua = req.headers.get('user-agent', '').lower()
        return any(k in ua for k in ['android', 'iphone', 'ipad', 'mobile', 'harmonyos'])

    if is_mobile(request):
        await render_mobile_status_page()
    else:
        await render_desktop_status_page()


# ================= 12. 移动端与桌面端视图 (被 router 调用) =================

# --- 全局变量用于视图状态 ---
CURRENT_PROBE_TAB = 'ALL'


# -------------------------------------------------------
# 1. 手机端视图 (独立优化，防止移动端渲染过重)
# -------------------------------------------------------
async def render_mobile_status_page():
    global CURRENT_PROBE_TAB
    mobile_refs = {}

    # 注入手机端专用 CSS
    ui.add_head_html('''
        <link href="https://fonts.googleapis.com/icon?family=Material+Icons" rel="stylesheet">
        <style>
            body { background-color: #0d0d0d; color: #ffffff; margin: 0; padding: 0; overflow-x: hidden; }
            .mobile-header { background: #1a1a1a; border-bottom: 1px solid #333; position: sticky; top: 0; z-index: 100; padding: 12px 16px; }
            .mobile-card-container { display: flex; flex-direction: column; align-items: center; width: 100%; padding: 12px 0; }
            .mobile-card { 
                background: #1a1a1a; border-radius: 16px; padding: 18px; border: 1px solid #333;
                width: calc(100% - 24px); margin-bottom: 16px; box-sizing: border-box;
            }
            .inner-module {
                background: #242424; border-radius: 12px; padding: 12px; height: 95px;
                display: flex; flex-direction: column; justify-content: space-between;
            }
            .stat-header { display: flex; justify-content: space-between; align-items: center; }
            .stat-label-box { display: flex; align-items: center; gap: 4px; }
            .stat-icon { font-size: 14px !important; color: #888; }
            .stat-label { color: #888; font-size: 11px; font-weight: bold; }
            .stat-value { color: #fff; font-size: 17px; font-weight: 800; font-family: monospace; }
            .bar-bg { height: 5px; background: #333; border-radius: 3px; overflow: hidden; margin: 2px 0; }
            .bar-fill-cpu { height: 100%; background: #3b82f6; transition: width 0.6s; box-shadow: 0 0 5px #3b82f6; }
            .bar-fill-mem { height: 100%; background: #22c55e; transition: width 0.6s; box-shadow: 0 0 5px #22c55e; }
            .bar-fill-disk { height: 100%; background: #a855f7; }
            .stat-subtext { color: #555; font-size: 10px; font-family: monospace; font-weight: bold; }
            .speed-up { color: #22c55e; font-weight: bold; font-size: 11px; }
            .speed-down { color: #3b82f6; font-weight: bold; font-size: 11px; }
            .scrollbar-hide::-webkit-scrollbar { display: none; }
        </style>
    ''')

    # 顶部标题栏
    with ui.column().classes('mobile-header w-full gap-1'):
        with ui.row().classes('w-full justify-between items-center'):
            ui.label('X-Fusion Status').classes('text-lg font-black text-blue-400')
            ui.button(icon='login', on_click=lambda: ui.navigate.to('/login')).props('flat dense color=grey-5')
        online_count = len([s for s in state.SERVERS_CACHE if s.get('_status') == 'online'])
        ui.label(f'🟢 {online_count} ONLINE / {len(state.SERVERS_CACHE)} TOTAL').classes(
            'text-[10px] font-bold text-gray-500 tracking-widest')

    # 分组标签页
    with ui.row().classes(
            'w-full px-2 py-1 bg-[#0d0d0d] border-b border-[#333] overflow-x-auto whitespace-nowrap scrollbar-hide'):
        groups = ['ALL'] + state.ADMIN_CONFIG.get('probe_custom_groups', [])
        with ui.tabs().props('dense no-caps active-color=blue-400 indicator-color=blue-400').classes(
                'text-gray-500') as tabs:
            for g in groups:
                ui.tab(g, label='全部' if g == 'ALL' else g).on('click', lambda _, group=g: update_mobile_tab(group))
            tabs.set_value(CURRENT_PROBE_TAB)

    list_container = ui.column().classes('mobile-card-container')

    # 渲染列表
    async def render_list(target_group):
        list_container.clear()
        mobile_refs.clear()

        # 筛选与排序
        filtered = [s for s in state.SERVERS_CACHE if target_group == 'ALL' or target_group in s.get('tags', [])]
        filtered.sort(key=lambda x: (0 if x.get('_status') == 'online' else 1, x.get('name', '')))

        with list_container:
            for s in filtered:
                status = state.PROBE_DATA_CACHE.get(s['url'], {})
                is_online = s.get('_status') == 'online'
                srv_ref = {}

                # 点击卡片打开详情
                with ui.column().classes('mobile-card').on('click', lambda _, srv=s: open_mobile_server_detail(srv)):
                    # 标题行
                    with ui.row().classes('items-center gap-3 mb-3'):
                        flag = "🏳️"
                        try:
                            flag = logic.detect_country_group(s['name'], s).split(' ')[0]
                        except:
                            pass
                        ui.label(flag).classes('text-3xl')
                        ui.label(s['name']).classes('text-base font-bold truncate').style('max-width:200px')

                    # 2x2 数据宫格
                    with ui.grid().classes('w-full grid-cols-2 gap-3'):
                        # CPU
                        cpu = status.get('cpu_usage', 0)
                        with ui.element('div').classes('inner-module'):
                            with ui.element('div').classes('stat-header'):
                                ui.html(
                                    '<div class="stat-label-box"><span class="material-icons stat-icon">settings_suggest</span><span class="stat-label">CPU</span></div>',
                                    sanitize=False)
                                srv_ref['cpu_text'] = ui.label(f'{cpu}%').classes('stat-value')
                            with ui.element('div').classes('bar-bg'):
                                srv_ref['cpu_bar'] = ui.element('div').classes('bar-fill-cpu').style(f'width: {cpu}%')
                            ui.label(f"{status.get('cpu_cores', 1)} Cores").classes('stat-subtext')

                        # RAM
                        mem_p = status.get('mem_usage', 0)
                        with ui.element('div').classes('inner-module'):
                            with ui.element('div').classes('stat-header'):
                                ui.html(
                                    '<div class="stat-label-box"><span class="material-icons stat-icon">memory</span><span class="stat-label">RAM</span></div>',
                                    sanitize=False)
                                srv_ref['mem_text'] = ui.label(f'{int(mem_p)}%').classes('stat-value')
                            with ui.element('div').classes('bar-bg'):
                                srv_ref['mem_bar'] = ui.element('div').classes('bar-fill-mem').style(f'width: {mem_p}%')
                            srv_ref['mem_detail'] = ui.label('-- / --').classes('stat-subtext')

                        # DISK
                        disk_p = status.get('disk_usage', 0)
                        with ui.element('div').classes('inner-module'):
                            with ui.element('div').classes('stat-header'):
                                ui.html(
                                    '<div class="stat-label-box"><span class="material-icons stat-icon">storage</span><span class="stat-label">DISK</span></div>',
                                    sanitize=False)
                                ui.label(f'{int(disk_p)}%').classes('stat-value')
                            with ui.element('div').classes('bar-bg'):
                                ui.element('div').classes('bar-fill-disk').style(f'width: {disk_p}%')
                            ui.label(f"{status.get('disk_total', 0)}G Total").classes('stat-subtext')

                        # NET
                        with ui.element('div').classes('inner-module'):
                            ui.html(
                                '<div class="stat-label-box"><span class="material-icons stat-icon">swap_calls</span><span class="stat-label">SPEED</span></div>',
                                sanitize=False)
                            with ui.column().classes('w-full gap-0'):
                                with ui.row().classes('w-full justify-between items-center'):
                                    ui.label('↑').classes('speed-up')
                                    srv_ref['net_up'] = ui.label('--').classes('text-[12px] font-mono font-bold')
                                with ui.row().classes('w-full justify-between items-center'):
                                    ui.label('↓').classes('speed-down')
                                    srv_ref['net_down'] = ui.label('--').classes('text-[12px] font-mono font-bold')

                    # 底部状态栏
                    with ui.row().classes('w-full justify-between mt-3 pt-2 border-t border-[#333] items-center'):
                        srv_ref['uptime'] = ui.label("在线时长：--").classes(
                            'text-[10px] font-bold text-green-500 font-mono')
                        with ui.row().classes('items-center gap-2'):
                            srv_ref['load'] = ui.label(f"⚡ {status.get('load_1', '0.0')}").classes(
                                'text-[10px] text-gray-400 font-bold')
                            ui.label('ACTIVE' if is_online else 'DOWN').classes(
                                f'text-[10px] font-black {"text-green-500" if is_online else "text-red-400"}')

                mobile_refs[s['url']] = srv_ref

    # 辅助：速度格式化
    def fmt_speed(b):
        if b < 1024: return f"{int(b)}B"
        return f"{int(b / 1024)}K" if b < 1024 ** 2 else f"{round(b / 1024 ** 2, 1)}M"

    # 循环刷新数据
    async def mobile_sync_loop():
        for url, refs in mobile_refs.items():
            status = state.PROBE_DATA_CACHE.get(url, {})
            if not status: continue

            refs['net_up'].set_text(f"{fmt_speed(status.get('net_speed_out', 0))}/s")
            refs['net_down'].set_text(f"{fmt_speed(status.get('net_speed_in', 0))}/s")

            cpu = status.get('cpu_usage', 0)
            mem_p = status.get('mem_usage', 0)
            refs['cpu_text'].set_text(f"{cpu}%")
            refs['cpu_bar'].style(f"width: {cpu}%")
            refs['mem_text'].set_text(f"{int(mem_p)}%")
            refs['mem_bar'].style(f"width: {mem_p}%")

            mem_t = status.get('mem_total', 0)
            mem_u = round(float(mem_t or 0) * (float(mem_p or 0) / 100), 2)
            refs['mem_detail'].set_text(f"{mem_u}G / {mem_t}G")

            raw_uptime = str(status.get('uptime', '-'))
            refs['uptime'].set_text(f"在线时长：{raw_uptime}")
            refs['load'].set_text(f"⚡ {status.get('load_1', '0.0')}")

    # 标签切换回调
    async def update_mobile_tab(val):
        global CURRENT_PROBE_TAB
        CURRENT_PROBE_TAB = val
        await render_list(val)

    # 初始执行
    await render_list(CURRENT_PROBE_TAB)
    ui.timer(2.0, mobile_sync_loop)


# -------------------------------------------------------
# 2. 电脑端视图 (包含地图、ECharts、详细卡片)
# -------------------------------------------------------
async def render_desktop_status_page():
    global CURRENT_PROBE_TAB

    # 1. 初始化暗色模式状态
    dark_mode = ui.dark_mode()
    if app.storage.user.get('is_dark') is None: app.storage.user['is_dark'] = True
    dark_mode.value = app.storage.user.get('is_dark')

    # 2. 注入 JS/CSS 资源
    ui.add_head_html('<script src="/static/echarts.min.js"></script>')
    ui.add_head_html('<link href="https://use.fontawesome.com/releases/v6.4.0/css/all.css" rel="stylesheet">')
    ui.add_head_html('''
        <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;700&family=Noto+Color+Emoji&display=swap" rel="stylesheet">
        <style>
            @font-face {
                font-family: 'Twemoji Country Flags';
                src: url('https://cdn.jsdelivr.net/npm/country-flag-emoji-polyfill@0.1/dist/TwemojiCountryFlags.woff2') format('woff2');
                unicode-range: U+1F1E6-1F1FF, U+1F3F4, U+E0062-E007F;
            }
            body { 
                margin: 0; 
                font-family: "Twemoji Country Flags", "Noto Color Emoji", "Segoe UI Emoji", "Noto Sans SC", sans-serif; 
                transition: background-color 0.3s ease; 
            }
            body:not(.body--dark) { background: linear-gradient(135deg, #e0c3fc 0%, #8ec5fc 100%); }
            body.body--dark { background-color: #0b1121; }

            .status-card { transition: all 0.3s ease; border-radius: 16px; }
            body:not(.body--dark) .status-card { background: rgba(255, 255, 255, 0.95); border: 1px solid rgba(255, 255, 255, 0.8); box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.1); color: #1e293b; }
            body.body--dark .status-card { background: #1e293b; border: 1px solid rgba(255,255,255,0.05); box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.3); color: #e2e8f0; }

            .status-card:hover { transform: translateY(-3px); }
            .offline-card { border-color: rgba(239, 68, 68, 0.6) !important; }

            body.body--dark .offline-card { background-image: repeating-linear-gradient(45deg, rgba(239, 68, 68, 0.05) 0px, rgba(239, 68, 68, 0.05) 10px, transparent 10px, transparent 20px) !important; }
            body:not(.body--dark) .offline-card { background: rgba(254, 226, 226, 0.95) !important; }

            .scrollbar-hide::-webkit-scrollbar { display: none; }
            .scrollbar-hide { -ms-overflow-style: none; scrollbar-width: none; }
            .prog-bar { transition: width 0.5s ease-out; }
            #public-map-container { contain: strict; transform: translateZ(0); will-change: transform; z-index: 0; }
        </style>
    ''')

    # 3. 状态与引用容器
    RENDERED_CARDS = {};
    tab_container = None;
    grid_container = None;
    header_refs = {};
    pie_chart_ref = None;
    pagination_ref = None
    local_ui_version = state.GLOBAL_UI_VERSION
    page_state = {'page': 1, 'group': 'ALL'}

    # 4. 准备初始地图数据
    try:
        chart_data, pie_data, region_count, region_stats_json, centroids_json = prepare_map_data()
    except Exception as e:
        chart_data = '{"cities": [], "flags": [], "regions": []}'
        pie_data = [];
        region_count = 0;
        region_stats_json = "{}";
        centroids_json = "{}"

    # 5. UI 结构：背景地图层
    with ui.element('div').classes('fixed top-0 left-0 w-full h-[35vh] min-h-[300px] max-h-[500px] z-0').style(
            'z-index: 0; contain: size layout paint;'):
        ui.html('<div id="public-map-container" style="width:100%; height:100%;"></div>', sanitize=False).classes(
            'w-full h-full')

    # 6. UI 结构：前景内容层
    with ui.column().classes(
            'w-full h-screen p-0 gap-0 overflow-hidden flex flex-col absolute top-0 left-0 pointer-events-none'):
        # 上半部分：透明区域 + 悬浮标题
        with ui.element('div').classes(
                'w-full h-[35vh] min-h-[300px] max-h-[500px] relative p-0 shrink-0 pointer-events-none'):
            # 悬浮 Header
            with ui.row().classes('absolute top-6 left-8 right-8 z-50 justify-between items-start pointer-events-auto'):
                with ui.column().classes('gap-1'):
                    with ui.row().classes('items-center gap-3'):
                        ui.icon('public', color='blue').classes('text-3xl drop-shadow-[0_0_10px_rgba(59,130,246,0.8)]')
                        ui.label('X-Fusion Status').classes(
                            'text-2xl font-black text-slate-800 dark:text-white drop-shadow-md')
                    with ui.row().classes('gap-4 text-sm font-bold font-mono pl-1'):
                        with ui.row().classes('items-center gap-1'):
                            ui.element('div').classes(
                                'w-2 h-2 rounded-full bg-green-500 shadow-[0_0_5px_rgba(34,197,94,0.8)]')
                            header_refs['online_count'] = ui.label('在线: --').classes(
                                'text-slate-600 dark:text-slate-300 drop-shadow-sm')
                        with ui.row().classes('items-center gap-1'):
                            ui.icon('language').classes('text-blue-500 dark:text-blue-400 text-xs drop-shadow-sm')
                            header_refs['region_count'] = ui.label(f'分布区域: {region_count}').classes(
                                'text-slate-600 dark:text-slate-300 drop-shadow-sm')

                # 右上角按钮
                with ui.row().classes('items-center gap-2'):
                    def toggle_dark():
                        dark_mode.value = not dark_mode.value
                        app.storage.user['is_dark'] = dark_mode.value
                        # 切换图表文字颜色
                        if pie_chart_ref:
                            color = '#e2e8f0' if dark_mode.value else '#334155'
                            pie_chart_ref.options['legend']['textStyle']['color'] = color
                            pie_chart_ref.update()
                        # 通知 JS 切换地图配色
                        ui.run_javascript(f'if(window.changeTheme) window.changeTheme({str(dark_mode.value).lower()});')

                    ui.button(icon='dark_mode', on_click=toggle_dark).props('flat round dense').classes(
                        'text-slate-700 dark:text-yellow-400 bg-white/50')
                    ui.button('后台管理', icon='login', on_click=lambda: ui.navigate.to('/login')).props(
                        'flat dense').classes(
                        'font-bold text-xs text-slate-700 dark:text-slate-300 bg-white/50 rounded px-2')

            # 左下角悬浮饼图
            with ui.element('div').classes('absolute left-4 bottom-4 z-40 pointer-events-auto'):
                text_color = '#e2e8f0' if dark_mode.value else '#334155'
                pie_chart_ref = ui.echart({'backgroundColor': 'transparent', 'tooltip': {'trigger': 'item'},
                                           'legend': {'bottom': '0%', 'left': 'center', 'icon': 'circle', 'itemGap': 15,
                                                      'textStyle': {'color': text_color, 'fontSize': 11}}, 'series': [
                        {'type': 'pie', 'radius': ['35%', '60%'], 'center': ['50%', '35%'], 'avoidLabelOverlap': False,
                         'itemStyle': {'borderRadius': 4, 'borderColor': 'transparent', 'borderWidth': 2},
                         'label': {'show': False}, 'emphasis': {'scale': True, 'scaleSize': 10,
                                                                'label': {'show': True, 'color': 'auto',
                                                                          'fontWeight': 'bold'},
                                                                'itemStyle': {'shadowBlur': 10, 'shadowOffsetX': 0,
                                                                              'shadowColor': 'rgba(0, 0, 0, 0.5)'}},
                         'data': pie_data}]}).classes('w-64 h-72')

        # 下半部分：服务器列表容器 (磨砂玻璃背景)
        with ui.column().classes(
                'w-full flex-grow relative gap-0 overflow-hidden flex flex-col bg-white/80 dark:bg-[#0f172a]/90 backdrop-blur-xl pointer-events-auto border-t border-white/10').style(
                'z-index: 10; contain: content;'):
            # 标签页与分页器栏
            with ui.row().classes(
                    'w-full px-6 py-2 border-b border-gray-200/50 dark:border-gray-800 items-center shrink-0 justify-between'):
                with ui.element('div').classes(
                    'flex-grow overflow-x-auto whitespace-nowrap scrollbar-hide mr-4') as tab_container: pass
                pagination_ref = ui.row().classes('items-center')

            # 滚动列表区
            with ui.scroll_area().classes('w-full flex-grow p-4 md:p-6'):
                grid_container = ui.grid().classes('w-full gap-4 md:gap-5 pb-20').style(
                    'grid-template-columns: repeat(auto-fill, minmax(320px, 1fr))')

    # ================= 渲染辅助函数 =================

    # 渲染顶部标签页
    def render_tabs():
        tab_container.clear()
        groups = ['ALL'] + state.ADMIN_CONFIG.get('probe_custom_groups', [])
        global CURRENT_PROBE_TAB
        if CURRENT_PROBE_TAB not in groups: CURRENT_PROBE_TAB = 'ALL'
        page_state['group'] = CURRENT_PROBE_TAB
        with tab_container:
            with ui.tabs().props('dense no-caps align=left active-color=blue indicator-color=blue').classes(
                    'text-slate-600 dark:text-gray-500 bg-transparent') as tabs:
                for g in groups:
                    ui.tab(g, label='全部' if g == 'ALL' else g).on('click', lambda _, g=g: apply_filter(g))
                tabs.set_value(CURRENT_PROBE_TAB)

    # 更新卡片 UI 数据的函数 (解耦逻辑)
    def update_card_ui(refs, status, static):
        if not status: return

        # 状态图标更新
        is_probe_online = (status.get('status') == 'online')
        if is_probe_online:
            refs['status_icon'].set_name('bolt');
            refs['status_icon'].classes(replace='text-green-500', remove='text-gray-400 text-red-500 text-purple-400')
            refs['online_dot'].classes(replace='bg-green-500', remove='bg-gray-500 bg-red-500 bg-purple-500')
        else:
            if status.get('cpu_usage') is not None:  # 有数据但离线 (如API通探针不通)
                refs['status_icon'].set_name('api');
                refs['status_icon'].classes(replace='text-purple-400',
                                            remove='text-gray-400 text-red-500 text-green-500')
                refs['online_dot'].classes(replace='bg-purple-500', remove='bg-gray-500 bg-red-500 bg-green-500')
            else:
                refs['status_icon'].set_name('flash_off');
                refs['status_icon'].classes(replace='text-red-500',
                                            remove='text-green-500 text-gray-400 text-purple-400')
                refs['online_dot'].classes(replace='bg-red-500', remove='bg-green-500 bg-orange-500 bg-purple-500')

        # 文本信息更新
        import re
        refs['os_info'].set_text(re.sub(r' GNU/Linux', '', static.get('os', 'Linux'), flags=re.I))
        refs['summary_cores'].set_text(f"{status.get('cpu_cores', static.get('cpu_cores'))} C")

        def fmt_cap(b):
            return utils.format_bytes(b)

        refs['summary_ram'].set_text(fmt_cap(status.get('mem_total', 0)))
        refs['summary_disk'].set_text(fmt_cap(status.get('disk_total', 0)))

        refs['traf_up'].set_text(f"↑ {fmt_cap(status.get('net_total_out', 0))}")
        refs['traf_down'].set_text(f"↓ {fmt_cap(status.get('net_total_in', 0))}")

        # 进度条更新
        cpu = float(status.get('cpu_usage', 0))
        refs['cpu_bar'].style(f'width: {cpu}%');
        refs['cpu_pct'].set_text(f'{cpu:.1f}%')
        refs['cpu_sub'].set_text(f"{status.get('cpu_cores', 1)} Cores")

        mem = float(status.get('mem_usage', 0))
        refs['mem_bar'].style(f'width: {mem}%');
        refs['mem_pct'].set_text(f'{mem:.1f}%')
        mem_t = float(status.get('mem_total', 0))
        refs['mem_sub'].set_text(f"{fmt_cap(mem_t * (mem / 100.0))} / {fmt_cap(mem_t)}")

        disk = float(status.get('disk_usage', 0))
        refs['disk_bar'].style(f'width: {disk}%');
        refs['disk_pct'].set_text(f'{disk:.1f}%')
        disk_t = float(status.get('disk_total', 0))
        refs['disk_sub'].set_text(f"{fmt_cap(disk_t * (disk / 100.0))} / {fmt_cap(disk_t)}")

        def fmt_s(b):
            return f"{utils.format_bytes(b)}/s"

        refs['net_up'].set_text(f"↑ {fmt_s(status.get('net_speed_out', 0))}")
        refs['net_down'].set_text(f"↓ {fmt_s(status.get('net_speed_in', 0))}")

        up = str(status.get('uptime', '-'))
        refs['uptime'].set_content(
            re.sub(r'(\d+)(\s*(?:days?|天))', r'<span class="text-green-500 font-bold text-sm">\1</span>\2', up,
                   flags=re.IGNORECASE))

    # 卡片自动更新循环
    async def card_autoupdate_loop(url):
        current_server = next((s for s in state.SERVERS_CACHE if s['url'] == url), None)
        # 仅探针机器开启轮询，节省资源
        if not current_server or not current_server.get('probe_installed', False): return

        await asyncio.sleep(random.uniform(0.5, 3.0))  # 错峰
        while True:
            if url not in RENDERED_CARDS: break
            item = RENDERED_CARDS.get(url)
            # 省流：如果卡片不可见，暂缓刷新
            if not item or not item['card'].visible: await asyncio.sleep(5.0); continue

            res = None
            try:
                # 获取数据
                res = await asyncio.wait_for(
                    logic.get_server_status(next((s for s in state.SERVERS_CACHE if s['url'] == url), None)),
                    timeout=5.0)
            except:
                pass

            if res:
                static = state.PROBE_DATA_CACHE.get(url, {}).get('static', {})
                update_card_ui(item['refs'], res, static)
                if res.get('status') == 'online':
                    item['card'].classes(remove='offline-card')
                else:
                    item['card'].classes(add='offline-card')

            await asyncio.sleep(random.uniform(2.0, 3.0))

    # 创建单个服务器卡片
    def create_server_card(s):
        url = s['url'];
        refs = {}
        # 尝试读取初始缓存，避免白屏
        initial_status = state.PROBE_DATA_CACHE.get(url, {}).copy() if url in state.PROBE_DATA_CACHE else None

        with grid_container:
            with ui.card().classes(
                    'status-card w-full p-4 md:p-5 flex flex-col gap-2 md:gap-3 relative overflow-hidden group').style(
                    'contain: content;') as card:
                refs['card'] = card

                # 第一行：国旗 + 名称 + 状态图标
                with ui.row().classes('w-full items-center mb-1 gap-2 flex-nowrap'):
                    flag = "🏳️";
                    try:
                        flag = logic.detect_country_group(s['name'], s).split(' ')[0]
                    except:
                        pass
                    ui.label(flag).classes('text-2xl md:text-3xl flex-shrink-0 leading-none')
                    ui.label(s['name']).classes(
                        'text-base md:text-lg font-bold text-slate-800 dark:text-gray-100 truncate flex-grow min-w-0 cursor-pointer hover:text-blue-500 transition leading-tight').on(
                        'click', lambda _, s=s: open_pc_server_detail(s))
                    refs['status_icon'] = ui.icon('bolt').props('size=32px').classes('text-gray-400 flex-shrink-0')

                # 第二行：OS 图标 + 系统信息
                with ui.row().classes('w-full justify-between items-center px-1 mb-2'):
                    with ui.row().classes('items-center gap-1.5'):
                        ui.icon('dns').classes('text-xs text-gray-400');
                        ui.label('OS').classes('text-xs text-slate-500 dark:text-gray-400 font-bold')
                    with ui.row().classes('items-center gap-1.5'):
                        refs['os_icon'] = ui.icon('computer').classes('text-xs text-slate-400');
                        refs['os_info'] = ui.label('Loading...').classes(
                            'text-xs font-mono font-bold text-slate-700 dark:text-gray-300 whitespace-nowrap')

                ui.separator().classes('mb-3 opacity-50 dark:opacity-30')

                # 第三行：核心指标概览 (CPU/RAM/DISK)
                with ui.row().classes('w-full justify-between px-1 mb-1 md:mb-2'):
                    label_cls = 'text-xs font-mono text-slate-500 dark:text-gray-400 font-bold'
                    with ui.row().classes('items-center gap-1'): ui.icon('grid_view').classes(
                        'text-blue-500 dark:text-blue-400 text-xs'); refs['summary_cores'] = ui.label('--').classes(
                        label_cls)
                    with ui.row().classes('items-center gap-1'): ui.icon('memory').classes(
                        'text-green-500 dark:text-green-400 text-xs'); refs['summary_ram'] = ui.label('--').classes(
                        label_cls)
                    with ui.row().classes('items-center gap-1'): ui.icon('storage').classes(
                        'text-purple-500 dark:text-purple-400 text-xs'); refs['summary_disk'] = ui.label('--').classes(
                        label_cls)

                # 第四行：进度条详情
                with ui.column().classes('w-full gap-2 md:gap-3'):
                    def stat_row(label, color_cls, light_track_color):
                        with ui.column().classes('w-full gap-1'):
                            with ui.row().classes('w-full items-center justify-between'):
                                ui.label(label).classes('text-xs text-slate-500 dark:text-gray-500 font-bold w-8')
                                with ui.element('div').classes(
                                        f'flex-grow h-2 md:h-2.5 bg-{light_track_color} dark:bg-gray-700/50 rounded-full overflow-hidden mx-2 transition-colors'):
                                    bar = ui.element('div').classes(f'h-full {color_cls} prog-bar').style('width: 0%')
                                pct = ui.label('0%').classes(
                                    'text-xs font-mono font-bold text-slate-700 dark:text-white w-8 text-right')
                            sub = ui.label('').classes(
                                'text-[10px] text-slate-400 dark:text-gray-500 font-mono text-right w-full pr-1')
                        return bar, pct, sub

                    refs['cpu_bar'], refs['cpu_pct'], refs['cpu_sub'] = stat_row('CPU', 'bg-blue-500', 'blue-100')
                    refs['mem_bar'], refs['mem_pct'], refs['mem_sub'] = stat_row('内存', 'bg-green-500', 'green-100')
                    refs['disk_bar'], refs['disk_pct'], refs['disk_sub'] = stat_row('硬盘', 'bg-purple-500',
                                                                                    'purple-100')

                ui.separator().classes('bg-slate-200 dark:bg-white/5 my-1')

                # 第五行：网络与在线时间
                with ui.column().classes('w-full gap-1'):
                    label_sub_cls = 'text-xs text-slate-400 dark:text-gray-500'
                    with ui.row().classes('w-full justify-between items-center no-wrap'):
                        ui.label('网络').classes(label_sub_cls);
                        with ui.row().classes('gap-2 font-mono whitespace-nowrap'): refs['net_up'] = ui.label(
                            '↑ 0B').classes('text-xs text-orange-500 dark:text-orange-400 font-bold'); refs[
                            'net_down'] = ui.label('↓ 0B').classes(
                            'text-xs text-green-600 dark:text-green-400 font-bold')
                    with ui.row().classes('w-full justify-between items-center no-wrap'):
                        ui.label('流量').classes(label_sub_cls)
                        with ui.row().classes(
                            'gap-2 font-mono whitespace-nowrap text-xs text-slate-600 dark:text-gray-300'): refs[
                            'traf_up'] = ui.label('↑ 0B'); refs['traf_down'] = ui.label('↓ 0B')
                    with ui.row().classes('w-full justify-between items-center no-wrap'):
                        ui.label('在线').classes(label_sub_cls)
                        with ui.row().classes('items-center gap-1'): refs['uptime'] = ui.html('--',
                                                                                              sanitize=False).classes(
                            'text-xs font-mono text-slate-600 dark:text-gray-300 text-right'); refs[
                            'online_dot'] = ui.element('div').classes('w-1.5 h-1.5 rounded-full bg-gray-400')

        # 如果有缓存，立即更新 UI
        if initial_status:
            static = state.PROBE_DATA_CACHE.get(url, {}).get('static', {})
            update_card_ui(refs, initial_status, static)
            if (initial_status.get('status') == 'online') or (initial_status.get('cpu_usage') is not None):
                card.classes(remove='offline-card')
            else:
                card.classes(add='offline-card')

        RENDERED_CARDS[url] = {'card': card, 'refs': refs, 'data': s}
        # 启动该卡片的独立刷新循环
        asyncio.create_task(card_autoupdate_loop(url))

    def apply_filter(group_name):
        global CURRENT_PROBE_TAB;
        CURRENT_PROBE_TAB = group_name
        page_state['group'] = group_name;
        page_state['page'] = 1
        render_grid_page()

    def change_page(new_page):
        page_state['page'] = new_page
        render_grid_page()

    # 渲染网格（包含分页逻辑）
    def render_grid_page():
        grid_container.clear();
        pagination_ref.clear();
        RENDERED_CARDS.clear()
        group_name = page_state['group']
        try:
            sorted_all = sorted(state.SERVERS_CACHE, key=lambda x: x.get('name', ''))
        except:
            sorted_all = state.SERVERS_CACHE
        filtered_servers = [s for s in sorted_all if group_name == 'ALL' or (group_name in s.get('tags', []))]

        PAGE_SIZE = 60;
        total = len(filtered_servers);
        pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
        if page_state['page'] > pages: page_state['page'] = 1
        if page_state['page'] < 1: page_state['page'] = 1

        start = (page_state['page'] - 1) * PAGE_SIZE;
        end = start + PAGE_SIZE
        current_page_items = filtered_servers[start:end]

        if not current_page_items:
            with grid_container:
                ui.label('暂无服务器').classes('text-gray-500 dark:text-gray-400 col-span-full text-center mt-10')
        else:
            for s in current_page_items: create_server_card(s)

        if pages > 1:
            with pagination_ref:
                p = ui.pagination(1, pages, direction_links=True).props(
                    'dense color=blue outline rounded text-color=white active-color=blue active-text-color=white max-pages=7')
                p.value = page_state['page']
                p.on('update:model-value', lambda e: change_page(e.args))
                ui.label(f'共 {total} 台').classes('text-xs text-gray-400 ml-4 self-center')

    # 初始化渲染
    render_tabs();
    render_grid_page()
    ui.run_javascript(config.GLOBE_JS_LOGIC.replace('window.DASHBOARD_DATA', chart_data))

    # 全局循环更新（仅更新统计数字，不重绘卡片）
    async def loop_update():
        nonlocal local_ui_version
        try:
            if state.GLOBAL_UI_VERSION != local_ui_version:
                local_ui_version = state.GLOBAL_UI_VERSION
                render_tabs();
                render_grid_page()
                try:
                    new_map, _, new_cnt, new_stats, new_centroids = prepare_map_data()
                except:
                    new_map = "{}"; new_cnt = 0; new_stats = "{}"; new_centroids = "{}"
                if header_refs.get('region_count'): header_refs['region_count'].set_text(f'分布区域: {new_cnt}')
                ui.run_javascript(
                    f'''if(window.updatePublicMap){{ window.regionStats = {new_stats}; window.countryCentroids = {new_centroids}; window.updatePublicMap({new_map}); }}''')

            real_online_count = 0
            now_ts = time.time()
            for s in state.SERVERS_CACHE:
                probe_cache = state.PROBE_DATA_CACHE.get(s['url'])
                if probe_cache and (now_ts - probe_cache.get('last_updated', 0) < 20):
                    real_online_count += 1
                elif s.get('_status') == 'online':
                    real_online_count += 1
            if header_refs.get('online_count'): header_refs['online_count'].set_text(f'在线: {real_online_count}')
        except:
            pass
        ui.timer(5.0, loop_update, once=True)

    ui.timer(0.1, loop_update, once=True)


# ================= 13. 批量操作工具 (类与函数) =================

class BatchSSH:
    def __init__(self):
        self.selected_urls = set()
        self.log_element = None
        self.dialog = None

    def open_dialog(self):
        self.selected_urls = set()
        with ui.dialog() as d, ui.card().classes('w-full max-w-4xl h-[80vh] flex flex-col p-0 overflow-hidden'):
            self.dialog = d
            with ui.row().classes('w-full justify-between items-center p-4 bg-gray-50 border-b'):
                ui.label('批量 SSH 执行').classes('text-lg font-bold')
                ui.button(icon='close', on_click=d.close).props('flat round dense color=grey')

            self.content_box = ui.column().classes('w-full flex-grow overflow-hidden p-0')
            self.render_selection_view()
        d.open()

    def render_selection_view(self):
        self.content_box.clear()
        with self.content_box:
            with ui.scroll_area().classes('w-full flex-grow p-4'):
                with ui.column().classes('w-full gap-2'):
                    ui.label('请选择要执行命令的服务器：').classes('text-sm text-gray-500 font-bold mb-2')
                    for s in state.SERVERS_CACHE:
                        with ui.row().classes(
                                'items-center p-2 border rounded hover:bg-gray-50 cursor-pointer w-full') as row:
                            chk = ui.checkbox(value=False).props('dense')

                            # 绑定点击事件
                            def toggle(c=chk, url=s['url']):
                                c.value = not c.value
                                if c.value:
                                    self.selected_urls.add(url)
                                else:
                                    self.selected_urls.discard(url)

                            chk.on_value_change(lambda e, u=s['url']: self.selected_urls.add(
                                u) if e.value else self.selected_urls.discard(u))
                            row.on('click', toggle)

                            ui.label(s['name']).classes('font-bold ml-2')
                            ui.label(s['url']).classes('text-xs text-gray-400 font-mono ml-auto')

            with ui.row().classes('w-full p-4 border-t bg-white justify-end'):
                ui.button('下一步: 输入命令', on_click=self.render_execution_view).classes('bg-slate-900 text-white')

    def render_execution_view(self):
        if not self.selected_urls:
            safe_notify('请至少选择一台服务器', 'warning')
            return

        self.content_box.clear()
        with self.content_box:
            with ui.row().classes('w-full h-full'):
                # 左侧命令区
                with ui.column().classes('w-1/3 h-full p-4 border-r gap-4'):
                    ui.label(f'已选 {len(self.selected_urls)} 台服务器').classes('font-bold text-gray-600')
                    cmd_input = ui.textarea(placeholder='请输入 Shell 命令...').classes(
                        'w-full flex-grow font-mono text-sm').props('outlined')

                    async def run_cmd():
                        user_cmd = cmd_input.value.strip()
                        if not user_cmd: return safe_notify('命令不能为空', 'warning')

                        log_area.push(f"\n======== 开始批量执行 (命令: {user_cmd}) ========\n")

                        for url in self.selected_urls:
                            srv = next((s for s in state.SERVERS_CACHE if s['url'] == url), None)
                            if not srv: continue

                            log_area.push(f"🚀 [{srv['name']}] 执行中...")
                            try:
                                success, output = await logic.run_in_bg_executor(
                                    lambda: utils._ssh_exec_wrapper(srv, user_cmd))
                                if success:
                                    log_area.push(f"✅ [{srv['name']}] 成功:\n{output}\n")
                                else:
                                    log_area.push(f"❌ [{srv['name']}] 失败:\n{output}\n")
                            except Exception as e:
                                log_area.push(f"❌ [{srv['name']}] 异常: {str(e)}\n")
                            log_area.push("-" * 50)

                        log_area.push("\n======== 执行结束 ========\n")

                    ui.button('立即执行', icon='play_arrow', on_click=run_cmd).classes('w-full bg-green-600 text-white')
                    ui.button('返回选择', on_click=self.render_selection_view).props('flat color=grey').classes(
                        'w-full')

                # 右侧日志区
                with ui.column().classes('w-2/3 h-full bg-black'):
                    log_area = ui.log().classes('w-full h-full p-4 text-xs font-mono text-green-400')


batch_ssh_manager = BatchSSH()


class BulkEditor:
    def __init__(self, target_servers, title="批量管理"):
        self.all_servers = target_servers
        self.title = title
        self.selected_urls = set()
        self.ui_rows = {}
        self.dialog = None

    def open(self):
        with ui.dialog() as d, ui.card().classes('w-full max-w-4xl h-[85vh] flex flex-col p-0 overflow-hidden'):
            self.dialog = d

            # --- 1. 顶部标题 ---
            with ui.row().classes('w-full justify-between items-center p-4 bg-gray-50 border-b flex-shrink-0'):
                with ui.row().classes('items-center gap-2'):
                    ui.icon('edit_note', color='primary').classes('text-xl')
                    ui.label(self.title).classes('text-lg font-bold')
                ui.button(icon='close', on_click=d.close).props('flat round dense color=grey')

            # --- 2. 工具栏 ---
            with ui.column().classes('w-full p-4 gap-3 border-b bg-white flex-shrink-0'):
                self.search_input = ui.input(placeholder='🔍 搜索服务器名称...').props(
                    'outlined dense clearable').classes('w-full')
                self.search_input.on_value_change(self.on_search)

                with ui.row().classes('w-full justify-between items-center'):
                    with ui.row().classes('gap-2'):
                        ui.button('全选', on_click=lambda: self.toggle_all(True)).props(
                            'flat dense size=sm color=primary')
                        ui.button('全不选', on_click=lambda: self.toggle_all(False)).props(
                            'flat dense size=sm color=grey')
                        self.count_label = ui.label('已选: 0').classes(
                            'text-xs font-bold text-gray-500 self-center ml-2')

            # --- 3. 列表区域 ---
            with ui.scroll_area().classes('w-full flex-grow p-2 bg-gray-50'):
                with ui.column().classes('w-full gap-1') as self.list_container:
                    if not self.all_servers:
                        ui.label('当前组无服务器').classes('w-full text-center text-gray-400 mt-10')

                    try:
                        sorted_srv = sorted(self.all_servers, key=lambda x: str(x.get('name', '')))
                    except:
                        sorted_srv = self.all_servers

                    for s in sorted_srv:
                        with ui.row().classes(
                                'w-full items-center p-2 bg-white rounded border border-gray-200 hover:border-blue-400 transition') as row:
                            chk = ui.checkbox(value=False).props('dense').classes('mr-2')
                            chk.on_value_change(lambda e, u=s['url']: self.on_check(u, e.value))

                            with ui.column().classes('gap-0 flex-grow overflow-hidden'):
                                display_name = s['name']
                                try:
                                    country = logic.detect_country_group(s['name'])
                                    flag = country.split(' ')[0]
                                    if flag not in s['name']:
                                        display_name = f"{flag} {s['name']}"
                                except:
                                    pass

                                ui.label(display_name).classes('text-sm font-bold text-gray-800 truncate')
                                ui.label(s['url']).classes('text-xs text-gray-400 font-mono truncate hidden')

                            ip_addr = utils.get_real_ip_display(s['url'])
                            status = s.get('_status')
                            stat_color = 'green-500' if status == 'online' else (
                                'red-500' if status == 'offline' else 'grey-400')
                            stat_icon = 'bolt' if status in ['online', 'offline'] else 'help_outline'

                            with ui.row().classes('items-center gap-1'):
                                ui.icon(stat_icon).classes(f'text-{stat_color} text-sm')
                                ip_lbl = ui.label(ip_addr).classes('text-xs font-mono text-gray-500')
                                utils.bind_ip_label(s['url'], ip_lbl)

                        self.ui_rows[s['url']] = {
                            'el': row,
                            'search_text': f"{s['name']} {s['url']} {ip_addr}".lower(),
                            'checkbox': chk
                        }

            # --- 4. 底部操作栏 ---
            with ui.row().classes('w-full p-4 border-t bg-white justify-between items-center flex-shrink-0'):
                with ui.row().classes('gap-2'):
                    ui.label('批量操作:').classes('text-sm font-bold text-gray-600 self-center')

                    # === 移动分组 ===
                    async def move_group():
                        if not self.selected_urls: return safe_notify('未选择服务器', 'warning')
                        with ui.dialog() as sub_d, ui.card().classes('w-80'):
                            ui.label('移动到分组').classes('font-bold mb-2')
                            groups = sorted(list({s.get('group') for s in state.SERVERS_CACHE if s.get('group')}))
                            if '默认分组' not in groups: groups.insert(0, '默认分组')

                            sel = ui.select(groups, label='选择或输入分组', with_input=True,
                                            new_value_mode='add-unique').classes('w-full')

                            ui.button('确定移动', on_click=lambda: do_move(sel.value)).classes(
                                'w-full mt-4 bg-blue-600 text-white')

                            async def do_move(target_group):
                                if not target_group: return
                                count = 0
                                for s in state.SERVERS_CACHE:
                                    if s['url'] in self.selected_urls:
                                        s['group'] = target_group
                                        count += 1

                                if 'custom_groups' not in state.ADMIN_CONFIG: state.ADMIN_CONFIG['custom_groups'] = []
                                if target_group not in state.ADMIN_CONFIG[
                                    'custom_groups'] and target_group != '默认分组':
                                    state.ADMIN_CONFIG['custom_groups'].append(target_group)
                                    await logic.save_admin_config()

                                await logic.save_servers()
                                sub_d.close();
                                self.dialog.close()
                                render_sidebar_content.refresh()
                                try:
                                    await refresh_content('ALL')
                                except:
                                    pass
                                safe_notify(f'已移动 {count} 个服务器到 [{target_group}]', 'positive')
                        sub_d.open()

                    ui.button('移动分组', icon='folder_open', on_click=move_group).props('flat dense color=blue')

                    # === 批量 SSH 设置 ===
                    async def batch_ssh_config():
                        if not self.selected_urls: return safe_notify('未选择服务器', 'warning')

                        with ui.dialog() as d_ssh, ui.card().classes('w-96 p-5 flex flex-col gap-3'):
                            with ui.row().classes('items-center gap-2 mb-1'):
                                ui.icon('vpn_key', color='teal').classes('text-xl')
                                ui.label('批量 SSH 配置').classes('text-lg font-bold')

                            ui.label(f'正在修改 {len(self.selected_urls)} 个服务器的连接信息').classes(
                                'text-xs text-gray-400')

                            ui.label('SSH 用户名').classes('text-xs font-bold text-gray-500 mt-2')
                            user_input = ui.input(placeholder='留空则保持原样 (不修改)').props(
                                'outlined dense').classes('w-full')

                            ui.label('认证方式').classes('text-xs font-bold text-gray-500 mt-2')
                            auth_opts = ['不修改', '全局密钥', '独立密码', '独立密钥']
                            auth_sel = ui.select(auth_opts, value='不修改').props(
                                'outlined dense options-dense').classes('w-full')

                            pwd_input = ui.input('输入新密码', password=True).props('outlined dense').classes('w-full')
                            pwd_input.bind_visibility_from(auth_sel, 'value', value='独立密码')

                            key_input = ui.textarea('输入新私钥', placeholder='-----BEGIN OPENSSH PRIVATE KEY-----') \
                                .props('outlined dense rows=4 input-class=text-xs font-mono').classes('w-full')
                            key_input.bind_visibility_from(auth_sel, 'value', value='独立密钥')

                            global_hint = ui.label('✅ 将统一使用全局 SSH 密钥连接').classes(
                                'text-xs text-green-600 bg-green-50 p-2 rounded w-full text-center')
                            global_hint.bind_visibility_from(auth_sel, 'value', value='全局密钥')

                            async def save_ssh_changes():
                                count = 0
                                target_user = user_input.value.strip()
                                target_auth = auth_sel.value

                                for s in state.SERVERS_CACHE:
                                    if s['url'] in self.selected_urls:
                                        changed = False
                                        if target_user: s['ssh_user'] = target_user; changed = True
                                        if target_auth != '不修改':
                                            s['ssh_auth_type'] = target_auth;
                                            changed = True
                                            if target_auth == '独立密码':
                                                s['ssh_password'] = pwd_input.value
                                            elif target_auth == '独立密钥':
                                                s['ssh_key'] = key_input.value
                                        if changed: count += 1

                                if count > 0:
                                    await logic.save_servers();
                                    d_ssh.close()
                                    safe_notify(f'✅ 已更新 {count} 个服务器的 SSH 配置', 'positive')
                                else:
                                    d_ssh.close(); safe_notify('未做任何修改', 'warning')

                            with ui.row().classes('w-full justify-end mt-4 gap-2'):
                                ui.button('取消', on_click=d_ssh.close).props('flat color=grey')
                                ui.button('保存配置', icon='save', on_click=save_ssh_changes).classes(
                                    'bg-teal-600 text-white shadow-md')
                        d_ssh.open()

                    ui.button('SSH 设置', icon='vpn_key', on_click=batch_ssh_config).props('flat dense color=teal')

                    # === 删除服务器 ===
                    async def delete_servers():
                        if not self.selected_urls: return safe_notify('未选择服务器', 'warning')
                        with ui.dialog() as sub_d, ui.card():
                            ui.label(f'确定删除 {len(self.selected_urls)} 个服务器?').classes('font-bold text-red-600')
                            with ui.row().classes('w-full justify-end mt-4'):
                                ui.button('取消', on_click=sub_d.close).props('flat')

                                async def confirm_del():
                                    state.SERVERS_CACHE[:] = [s for s in state.SERVERS_CACHE if
                                                              s['url'] not in self.selected_urls]
                                    await logic.save_servers()
                                    sub_d.close();
                                    d.close()
                                    render_sidebar_content.refresh()
                                    if content_container: content_container.clear()
                                    safe_notify('删除成功', 'positive')

                                ui.button('确定删除', color='red', on_click=confirm_del)
                        sub_d.open()

                    ui.button('删除', icon='delete', on_click=delete_servers).props('flat dense color=red')

                ui.button('关闭', on_click=d.close).props('outline color=grey')
        d.open()

    def on_search(self, e):
        keyword = str(e.value).lower().strip()
        for url, item in self.ui_rows.items():
            visible = keyword in item['search_text']
            item['el'].set_visibility(visible)

    def on_check(self, url, value):
        if value:
            self.selected_urls.add(url)
        else:
            self.selected_urls.discard(url)
        self.count_label.set_text(f'已选: {len(self.selected_urls)}')

    def toggle_all(self, state):
        visible_urls = [u for u, item in self.ui_rows.items() if item['el'].visible]
        for url in visible_urls:
            self.ui_rows[url]['checkbox'].value = state
        if not state:
            for url in visible_urls: self.selected_urls.discard(url)
        self.count_label.set_text(f'已选: {len(self.selected_urls)}')


def open_bulk_edit_dialog(servers, title="管理"):
    editor = BulkEditor(servers, title)
    editor.open()


def open_combined_group_management(group_name):
    # 此函数为分组管理入口（与 open_unified_group_manager 类似但用于单组）
    # 为保证完整性保留
    open_unified_group_manager(mode='manage')


def open_data_mgmt_dialog():
    with ui.dialog() as d, ui.card().classes('w-[500px] p-6 flex flex-col gap-4'):
        with ui.row().classes('items-center gap-2 mb-2'):
            ui.icon('save', color='primary').classes('text-2xl')
            ui.label('数据备份与恢复').classes('text-xl font-bold')

        ui.label('备份当前所有数据 (servers.json, nodes.json, etc)').classes('text-sm text-gray-500')

        async def do_backup():
            try:
                zip_path = await logic.create_backup_zip()
                if zip_path:
                    safe_notify(f'备份成功: {zip_path}', 'positive')
                    ui.download(zip_path)
                else:
                    safe_notify('备份失败', 'negative')
            except Exception as e:
                safe_notify(f'异常: {e}', 'negative')

        ui.button('下载备份 (ZIP)', icon='download', on_click=do_backup).classes('w-full bg-blue-600 text-white')

        ui.separator()
        ui.label('恢复数据 (请上传 ZIP)').classes('text-sm text-gray-500')

        async def handle_upload(e):
            try:
                content = e.content.read()
                success = await logic.restore_backup_zip(content)
                if success:
                    safe_notify('✅ 数据恢复成功，正在刷新...', 'positive')
                    await asyncio.sleep(1)
                    ui.open('/')
                else:
                    safe_notify('恢复失败，请检查文件格式', 'negative')
            except Exception as ex:
                safe_notify(f'上传异常: {ex}', 'negative')

        ui.upload(label='拖拽或点击上传', on_upload=handle_upload, auto_upload=True).props('accept=.zip').classes(
            'w-full')

        with ui.row().classes('w-full justify-end mt-2'):
            ui.button('关闭', on_click=d.close).props('flat')
    d.open()


# ================= 小巧卡片式弹窗 (修复版：增加 X-UI 节点命名策略) =================
async def open_server_dialog(idx=None):
    is_edit = idx is not None
    original_data = state.SERVERS_CACHE[idx] if is_edit else {}
    data = original_data.copy()

    # --- 1. 智能检测初始状态 ---
    if is_edit:
        has_xui_conf = bool(data.get('url') and data.get('user') and data.get('pass'))
        raw_ssh_host = data.get('ssh_host')
        if not raw_ssh_host and not has_xui_conf:
            raw_ssh_host = data.get('url', '').replace('http://', '').replace('https://', '').split(':')[0]

        has_ssh_conf = bool(
            raw_ssh_host or
            data.get('ssh_user') or
            data.get('ssh_key') or
            data.get('ssh_password') or
            data.get('probe_installed')
        )
        if not has_ssh_conf and not has_xui_conf: has_ssh_conf = True
    else:
        has_xui_conf = True;
        has_ssh_conf = True

    dialog_state = {'ssh_active': has_ssh_conf, 'xui_active': has_xui_conf}

    with ui.dialog() as d, ui.card().classes('w-full max-w-sm p-5 flex flex-col gap-4'):

        # --- 标题栏 ---
        with ui.row().classes('w-full justify-between items-center'):
            ui.label('编辑服务器' if is_edit else '添加服务器').classes('text-lg font-bold')
            tabs = ui.tabs().classes('text-blue-600')
            with tabs:
                t_ssh = ui.tab('SSH / 探针', icon='terminal')
                t_xui = ui.tab('X-UI面板', icon='settings')

        # ================= 独立的基础信息保存逻辑 =================
        async def save_basic_info_only():
            if not is_edit:
                safe_notify("新增服务器请使用下方的保存按钮", "warning")
                return

            new_name = name_input.value.strip()
            new_group = group_input.value

            # 自动命名 (如果为空)
            if not new_name:
                safe_notify("正在生成智能名称...", "ongoing")
                new_name = await generate_smart_name(data)

            state.SERVERS_CACHE[idx]['name'] = new_name
            state.SERVERS_CACHE[idx]['group'] = new_group

            await logic.save_servers()
            render_sidebar_content.refresh()

            # 同步刷新
            current_scope = state.CURRENT_VIEW_STATE.get('scope')
            if current_scope == 'SINGLE' and state.CURRENT_VIEW_STATE.get('data') == state.SERVERS_CACHE[idx]:
                try:
                    await refresh_content('SINGLE', state.SERVERS_CACHE[idx])
                except:
                    pass
            elif current_scope in ['ALL', 'TAG', 'COUNTRY']:
                state.CURRENT_VIEW_STATE['scope'] = None
                try:
                    await refresh_content(current_scope, state.CURRENT_VIEW_STATE.get('data'), force_refresh=False)
                except:
                    pass

            safe_notify("✅ 基础信息已更新", "positive")
            d.close()

        # --- 通用字段区域 ---
        all_groups = sorted(
            list(set(s.get('group', '默认分组') for s in state.SERVERS_CACHE)) + state.ADMIN_CONFIG.get('custom_groups',
                                                                                                        []))
        if '默认分组' not in all_groups: all_groups.insert(0, '默认分组')

        with ui.column().classes('w-full gap-2'):
            name_input = ui.input(value=data.get('name', ''), label='备注名称 (留空自动获取)').classes('w-full').props(
                'outlined dense')

            with ui.row().classes('w-full items-center gap-2 no-wrap'):
                group_input = ui.select(options=all_groups, value=data.get('group', '默认分组'),
                                        new_value_mode='add-unique', label='分组').classes('flex-grow').props(
                    'outlined dense')

                if is_edit:
                    ui.button(icon='save', on_click=save_basic_info_only) \
                        .props('flat dense round color=primary') \
                        .tooltip('仅保存名称和分组 (不重新部署)')

        inputs = {}
        btn_keycap_blue = 'bg-white rounded-lg font-bold tracking-wide border-t border-x border-gray-100 border-b-4 border-blue-100 text-blue-600 px-4 py-1 transition-all duration-100 active:border-b-0 active:border-t-4 active:translate-y-1 hover:bg-blue-50'
        btn_keycap_delete = 'bg-white rounded-xl font-bold tracking-wide w-full border-t border-x border-gray-100 border-b-4 border-red-100 text-red-500 transition-all duration-100 active:border-b-0 active:border-t-4 active:translate-y-1 hover:bg-red-50'
        btn_keycap_red_confirm = 'rounded-lg font-bold tracking-wide text-white border-b-4 border-red-900 transition-all duration-100 active:border-b-0 active:border-t-4 active:translate-y-1'

        # ==================== 保存逻辑 (包含自动命名策略) ====================
        async def save_panel_data(panel_type):
            final_name = name_input.value.strip()
            final_group = group_input.value
            new_server_data = data.copy()
            new_server_data['group'] = final_group

            # 1. 收集面板数据
            if panel_type == 'ssh':
                if not inputs.get('ssh_host'): return
                s_host = inputs['ssh_host'].value.strip()
                if not s_host: safe_notify("SSH 主机 IP 不能为空", "negative"); return

                new_server_data.update({
                    'ssh_host': s_host,
                    'ssh_port': int(inputs['ssh_port'].value),
                    'ssh_user': inputs['ssh_user'].value.strip(),
                    'ssh_auth_type': inputs['auth_type'].value,
                    'ssh_password': inputs['ssh_pwd'].value if inputs['ssh_pwd'] else '',
                    'ssh_key': inputs['ssh_key'].value if inputs['ssh_key'] else '',
                    'probe_installed': True
                })

                if 'probe_chk' in inputs: inputs['probe_chk'].value = True
                if not new_server_data.get('url'): new_server_data['url'] = f"http://{s_host}:22"

            elif panel_type == 'xui':
                if not inputs.get('xui_url'): return
                x_url_raw = inputs['xui_url'].value.strip()
                x_user = inputs['xui_user'].value.strip()
                x_pass = inputs['xui_pass'].value.strip()

                if not (x_url_raw and x_user and x_pass):
                    safe_notify("必填项不能为空", "negative");
                    return

                if '://' not in x_url_raw: x_url_raw = f"http://{x_url_raw}"
                try:
                    parts = x_url_raw.split('://')
                    body = parts[1]
                    if ':' not in body:
                        x_url_raw = f"{x_url_raw}:54321"
                except:
                    pass

                probe_val = inputs['probe_chk'].value
                new_server_data.update({
                    'url': x_url_raw, 'user': x_user, 'pass': x_pass,
                    'prefix': inputs['xui_prefix'].value.strip(),
                    'probe_installed': probe_val
                })

                if probe_val:
                    if not new_server_data.get('ssh_host'):
                        if '://' in x_url_raw:
                            new_server_data['ssh_host'] = x_url_raw.split('://')[-1].split(':')[0]
                        else:
                            new_server_data['ssh_host'] = x_url_raw.split(':')[0]
                    if not new_server_data.get('ssh_port'): new_server_data['ssh_port'] = 22
                    if not new_server_data.get('ssh_user'): new_server_data['ssh_user'] = 'root'
                    if not new_server_data.get('ssh_auth_type'): new_server_data['ssh_auth_type'] = '全局密钥'

            # 2. 自动命名逻辑 (核心修改)
            if not final_name:
                name_found = False

                # 策略 1: 优先尝试连接面板获取第一个节点的备注
                if new_server_data.get('url') and new_server_data.get('user'):
                    safe_notify("正在尝试连接面板获取节点名称...", "ongoing")
                    try:
                        # 强制刷新一次节点列表
                        nodes = await logic.fetch_inbounds_safe(new_server_data, force_refresh=True)
                        if nodes and len(nodes) > 0:
                            first_remark = nodes[0].get('remark', '').strip()
                            if first_remark:
                                final_name = first_remark
                                name_found = True
                                safe_notify(f"已使用节点名命名: {final_name}", "positive")
                    except Exception as e:
                        print(f"Name fetch failed: {e}")

                # 策略 2: 如果策略 1 失败，回退到 GeoIP 智能命名
                if not name_found:
                    safe_notify("无法获取节点名，正在解析 IP 归属地...", "ongoing")
                    final_name = await generate_smart_name(new_server_data)

            new_server_data['name'] = final_name

            # 3. 自动分组 (根据最终的名称)
            if final_group == '默认分组' or not final_group:
                auto_g = logic.detect_country_group(final_name, new_server_data)
                if auto_g and auto_g != '🏳️ 其他地区':
                    new_server_data['group'] = auto_g
                else:
                    new_server_data['group'] = final_group
            else:
                new_server_data['group'] = final_group

            # 4. 执行保存
            if is_edit:
                state.SERVERS_CACHE[idx] = new_server_data
            else:
                state.SERVERS_CACHE.append(new_server_data)

            await logic.save_servers()
            render_sidebar_content.refresh()
            if state.CURRENT_VIEW_STATE['scope'] == 'ALL': await refresh_content('ALL')

            if new_server_data.get('probe_installed'):
                safe_notify(f"🚀 配置已保存，正在自动推送探针...", "ongoing")
                asyncio.create_task(logic.batch_install_all_probes())
            else:
                safe_notify(f"✅ {panel_type.upper()} 已保存", "positive")
            d.close()

        # ==================== SSH 面板渲染 ====================
        @ui.refreshable
        def render_ssh_panel():
            if not dialog_state['ssh_active']:
                with ui.column().classes(
                        'w-full h-48 justify-center items-center bg-gray-50 rounded border border-dashed border-gray-300'):
                    ui.icon('terminal', color='grey').classes('text-4xl mb-2')
                    ui.label('SSH 功能未启用').classes('text-gray-500 font-bold mb-2')
                    ui.button('启用 SSH 配置', icon='add', on_click=lambda: _activate_panel('ssh')).props(
                        'flat bg-blue-50 text-blue-600')
            else:
                init_host = data.get('ssh_host')
                if not init_host and is_edit:
                    if '://' in data.get('url', ''):
                        init_host = data.get('url', '').split('://')[-1].split(':')[0]
                    else:
                        init_host = data.get('url', '').split(':')[0]

                inputs['ssh_host'] = ui.input(label='SSH 主机 IP', value=init_host).classes('w-full').props(
                    'outlined dense')

                with ui.column().classes('w-full gap-3'):
                    with ui.row().classes('w-full gap-2'):
                        inputs['ssh_user'] = ui.input(value=data.get('ssh_user', 'root'), label='SSH 用户').classes(
                            'flex-1').props('outlined dense')
                        inputs['ssh_port'] = ui.input(value=data.get('ssh_port', 22), label='端口').classes(
                            'w-1/3').props('outlined dense')

                    valid_auth_options = ['全局密钥', '独立密码', '独立密钥']
                    current_auth = data.get('ssh_auth_type', '全局密钥')
                    if current_auth not in valid_auth_options: current_auth = '全局密钥'

                    inputs['auth_type'] = ui.select(valid_auth_options, value=current_auth, label='认证方式').classes(
                        'w-full').props('outlined dense options-dense')

                    inputs['ssh_pwd'] = ui.input(label='SSH 密码', password=True,
                                                 value=data.get('ssh_password', '')).classes('w-full').props(
                        'outlined dense')
                    inputs['ssh_pwd'].bind_visibility_from(inputs['auth_type'], 'value', value='独立密码')

                    inputs['ssh_key'] = ui.textarea(label='SSH 私钥', value=data.get('ssh_key', '')).classes(
                        'w-full').props('outlined dense rows=3 input-class=font-mono text-xs')
                    inputs['ssh_key'].bind_visibility_from(inputs['auth_type'], 'value', value='独立密钥')

                ui.separator().classes('my-1')
                with ui.row().classes('w-full justify-between items-center'):
                    ui.label('✅ 自动使用全局私钥').bind_visibility_from(inputs['auth_type'], 'value',
                                                                        value='全局密钥').classes(
                        'text-green-600 text-xs font-bold')
                    ui.element('div').bind_visibility_from(inputs['auth_type'], 'value', value='独立密码')
                    ui.element('div').bind_visibility_from(inputs['auth_type'], 'value', value='独立密钥')

                    ui.button('保存 SSH', icon='save', on_click=lambda: save_panel_data('ssh')).props('flat').classes(
                        btn_keycap_blue)

        # ==================== X-UI 面板渲染 ====================
        @ui.refreshable
        def render_xui_panel():
            if not dialog_state['xui_active']:
                with ui.column().classes(
                        'w-full h-48 justify-center items-center bg-gray-50 rounded border border-dashed border-gray-300'):
                    ui.icon('settings_applications', color='grey').classes('text-4xl mb-2')
                    ui.label('X-UI 面板未配置').classes('text-gray-500 font-bold mb-2')
                    ui.button('配置 X-UI 信息', icon='add', on_click=lambda: _activate_panel('xui')).props(
                        'flat bg-purple-50 text-purple-600')
            else:
                inputs['xui_url'] = ui.input(value=data.get('url', ''), label='面板 URL (http://ip:port)').classes(
                    'w-full').props('outlined dense')
                ui.label('默认端口 54321，如不填写将自动补全').classes('text-[10px] text-gray-400 ml-1 -mt-1 mb-1')

                with ui.row().classes('w-full gap-2'):
                    inputs['xui_user'] = ui.input(value=data.get('user', ''), label='账号').classes('flex-1').props(
                        'outlined dense')
                    inputs['xui_pass'] = ui.input(value=data.get('pass', ''), label='密码', password=True).classes(
                        'flex-1').props('outlined dense')
                inputs['xui_prefix'] = ui.input(value=data.get('prefix', ''), label='API 前缀 (选填)').classes(
                    'w-full').props('outlined dense')

                ui.separator().classes('my-1')

                with ui.row().classes('w-full justify-between items-center'):
                    inputs['probe_chk'] = ui.checkbox('启用 Root 探针', value=data.get('probe_installed', False))
                    inputs['probe_chk'].classes('text-sm font-bold text-slate-700')

                    ui.button('保存 X-UI', icon='save', on_click=lambda: save_panel_data('xui')).props('flat').classes(
                        btn_keycap_blue)

                ui.label('提示: 启用探针需先配置 SSH 登录信息').classes('text-[10px] text-red-500 ml-8 -mt-2')

                def auto_fill_ssh():
                    if inputs['probe_chk'].value and dialog_state['ssh_active'] and inputs.get('ssh_host') and not \
                    inputs['ssh_host'].value:
                        p_url = inputs['xui_url'].value
                        if p_url:
                            clean_ip = p_url.split('://')[-1].split(':')[0]
                            if ':' in clean_ip: clean_ip = clean_ip.split(':')[0]
                            inputs['ssh_host'].set_value(clean_ip)

                inputs['probe_chk'].on_value_change(auto_fill_ssh)

        def _activate_panel(panel_type):
            dialog_state[f'{panel_type}_active'] = True
            if panel_type == 'ssh':
                render_ssh_panel.refresh()
            elif panel_type == 'xui':
                render_xui_panel.refresh()

        default_tab = t_ssh
        if is_edit and not dialog_state['ssh_active'] and dialog_state['xui_active']: default_tab = t_xui

        with ui.tab_panels(tabs, value=default_tab).classes('w-full animated fadeIn'):
            with ui.tab_panel(t_ssh).classes('p-0 flex flex-col gap-3'):
                render_ssh_panel()
            with ui.tab_panel(t_xui).classes('p-0 flex flex-col gap-3'):
                render_xui_panel()

        # ================= 5. 全局删除逻辑 =================
        if is_edit:
            with ui.row().classes('w-full justify-start mt-4 pt-2 border-t border-gray-100'):
                async def open_delete_confirm():
                    with ui.dialog() as del_d, ui.card().classes('w-80 p-4'):
                        ui.label('删除确认').classes('text-lg font-bold text-red-600')
                        ui.label('请选择要删除的内容：').classes('text-sm text-gray-600 mb-2')

                        real_ssh_exists = bool(data.get('ssh_host') or data.get('ssh_user'))
                        real_xui_exists = bool(data.get('url') and data.get('user') and data.get('pass'))
                        has_probe = data.get('probe_installed', False)

                        if not real_ssh_exists and not real_xui_exists:
                            real_ssh_exists = True;
                            real_xui_exists = True

                        chk_ssh = ui.checkbox('SSH 连接信息', value=real_ssh_exists).classes('text-sm font-bold')
                        chk_xui = ui.checkbox('X-UI 面板信息', value=real_xui_exists).classes('text-sm font-bold')

                        chk_uninstall = ui.checkbox('同时卸载远程探针脚本', value=True).classes(
                            'text-sm font-bold text-red-500')
                        chk_uninstall.set_visibility(has_probe)

                        if not real_ssh_exists: chk_ssh.value = False; chk_ssh.disable()
                        if not real_xui_exists: chk_xui.value = False; chk_xui.disable()
                        if real_ssh_exists and not real_xui_exists: chk_ssh.disable()
                        if real_xui_exists and not real_ssh_exists: chk_xui.disable()

                        async def confirm_execution():
                            if idx >= len(state.SERVERS_CACHE): return
                            target_srv = state.SERVERS_CACHE[idx]

                            will_delete_ssh = chk_ssh.value
                            will_delete_xui = chk_xui.value
                            will_uninstall = chk_uninstall.value and chk_uninstall.visible

                            remaining_ssh = real_ssh_exists and not will_delete_ssh
                            remaining_xui = real_xui_exists and not will_delete_xui

                            is_full_delete = False

                            if will_uninstall:
                                loading_notify = ui.notification('正在尝试连接并卸载探针...', timeout=None,
                                                                 spinner=True)
                                try:
                                    uninstall_cmd = "systemctl stop x-fusion-agent && systemctl disable x-fusion-agent && rm -f /etc/systemd/system/x-fusion-agent.service && systemctl daemon-reload && rm -f /root/x_fusion_agent.py"
                                    # 🟢 [修复核心] 移除 lambda，直接传参
                                    success, output = await logic.run_in_bg_executor(utils._ssh_exec_wrapper,
                                                                                     target_srv, uninstall_cmd)

                                    if success:
                                        ui.notify('✅ 远程探针已卸载清理', type='positive')
                                    else:
                                        ui.notify(f'⚠️ 远程卸载失败，将仅删除本地记录', type='warning')
                                finally:
                                    loading_notify.dismiss()

                            if not remaining_ssh and not remaining_xui:
                                state.SERVERS_CACHE.pop(idx)
                                u = target_srv.get('url');
                                p_u = target_srv.get('ssh_host') or u
                                for k in [u, p_u]:
                                    if k in state.PROBE_DATA_CACHE: del state.PROBE_DATA_CACHE[k]
                                    if k in state.NODES_DATA: del state.NODES_DATA[k]
                                    if k in state.PING_TREND_CACHE: del state.PING_TREND_CACHE[k]
                                safe_notify('✅ 服务器已彻底删除', 'positive')
                                is_full_delete = True
                            else:
                                if will_delete_ssh:
                                    for k in ['ssh_host', 'ssh_port', 'ssh_user', 'ssh_password', 'ssh_key',
                                              'ssh_auth_type']: target_srv[k] = ''
                                    target_srv['probe_installed'] = False
                                    dialog_state['ssh_active'] = False
                                    data['ssh_host'] = ''
                                    safe_notify('✅ SSH 信息已清除', 'positive')

                                if will_delete_xui:
                                    for k in ['url', 'user', 'pass', 'prefix']: target_srv[k] = ''
                                    dialog_state['xui_active'] = False
                                    data['url'] = ''
                                    safe_notify('✅ X-UI 信息已清除', 'positive')

                            await logic.save_servers()
                            del_d.close()
                            d.close()

                            render_sidebar_content.refresh()
                            current_scope = state.CURRENT_VIEW_STATE.get('scope')
                            current_data = state.CURRENT_VIEW_STATE.get('data')

                            if is_full_delete:
                                if current_scope == 'SINGLE' and current_data == target_srv:
                                    content_container.clear()
                                    with content_container:
                                        ui.label('该服务器已删除').classes(
                                            'text-gray-400 text-lg w-full text-center mt-20')
                                elif current_scope in ['ALL', 'TAG', 'COUNTRY']:
                                    state.CURRENT_VIEW_STATE['scope'] = None
                                    await refresh_content(current_scope, current_data, force_refresh=False)
                            else:
                                if current_scope == 'SINGLE' and current_data == target_srv:
                                    await refresh_content('SINGLE', target_srv)

                        with ui.row().classes('w-full justify-end mt-4 gap-2'):
                            ui.button('取消', on_click=del_d.close).props('flat dense color=grey')
                            ui.button('确认执行', color='red', on_click=confirm_execution).props('unelevated').classes(
                                btn_keycap_red_confirm)
                    del_d.open()

                ui.button('删除 / 卸载配置', icon='delete', on_click=open_delete_confirm).props('flat').classes(
                    btn_keycap_delete)
    d.open()

# ================= 14. 底部状态绑定 (必须在最后) =================
# 将 UI 刷新函数绑定到 State，以便 Logic 层调用
async def _refresh_dashboard_wrapper():
    await refresh_dashboard_ui()
state.refresh_dashboard_ui_func = _refresh_dashboard_wrapper

async def _render_sidebar_wrapper():
    render_sidebar_content.refresh()
state.render_sidebar_content_func = render_sidebar_content

async def _load_dashboard_wrapper():
    await load_dashboard_stats()
state.load_dashboard_stats_func = _load_dashboard_wrapper