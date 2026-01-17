import io
import base64
import asyncio
import socket
import paramiko
import uuid
from nicegui import ui, run
from app.core.data_manager import load_global_key


def get_ssh_client_sync(server_data):
    """建立 SSH 连接 (同步阻塞方法)"""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    # 解析 IP
    raw_url = server_data['url']
    if '://' in raw_url:
        host = raw_url.split('://')[-1].split(':')[0]
    else:
        host = raw_url.split(':')[0]

    # 优先使用 ssh_host
    if server_data.get('ssh_host'): host = server_data['ssh_host']

    port = int(server_data.get('ssh_port') or 22)
    user = server_data.get('ssh_user') or 'root'
    auth_type = server_data.get('ssh_auth_type', '全局密钥').strip()

    try:
        if auth_type == '独立密码':
            pwd = server_data.get('ssh_password', '')
            if not pwd: raise Exception("选择了独立密码，但密码为空")
            client.connect(host, port, username=user, password=pwd, timeout=5, look_for_keys=False, allow_agent=False)

        elif auth_type == '独立密钥':
            key_content = server_data.get('ssh_key', '')
            if not key_content: raise Exception("选择了独立密钥，但密钥为空")
            key_file = io.StringIO(key_content)
            try:
                pkey = paramiko.RSAKey.from_private_key(key_file)
            except:
                key_file.seek(0)
                try:
                    pkey = paramiko.Ed25519Key.from_private_key(key_file)
                except:
                    raise Exception("无法识别的私钥格式")
            client.connect(host, port, username=user, pkey=pkey, timeout=5, look_for_keys=False, allow_agent=False)

        else:  # 默认：全局密钥
            g_key = load_global_key()
            if not g_key: raise Exception("全局密钥未配置")
            key_file = io.StringIO(g_key)
            try:
                pkey = paramiko.RSAKey.from_private_key(key_file)
            except:
                key_file.seek(0)
                try:
                    pkey = paramiko.Ed25519Key.from_private_key(key_file)
                except:
                    raise Exception("全局密钥格式无法识别")
            client.connect(host, port, username=user, pkey=pkey, timeout=5, look_for_keys=False, allow_agent=False)

        return client, f"✅ 已连接 {user}@{host}"

    except Exception as e:
        return None, f"❌ 连接失败: {str(e)}"


def _ssh_exec_wrapper(server_conf, cmd):
    """辅助函数：执行命令并返回结果"""
    client, msg = get_ssh_client_sync(server_conf)
    if not client: return False, msg
    try:
        stdin, stdout, stderr = client.exec_command(cmd, timeout=120)
        out = stdout.read().decode().strip()
        err = stderr.read().decode().strip()
        client.close()
        return True, out + "\n" + err
    except Exception as e:
        return False, str(e)


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
            # 1. 渲染终端 UI
            ui.element('div').props(f'id={self.term_id}').classes(
                'w-full h-full bg-black rounded p-2 overflow-hidden relative')

            # 2. 注入 JS
            init_js = f"""
            try {{
                if (window.{self.term_id}) {{ if (typeof window.{self.term_id}.dispose === 'function') window.{self.term_id}.dispose(); window.{self.term_id} = null; }}
                var term = new Terminal({{ cursorBlink: true, fontSize: 13, fontFamily: 'Menlo, Monaco, "Courier New", monospace', theme: {{ background: '#000000', foreground: '#ffffff' }}, convertEol: true, scrollback: 5000 }});
                var fitAddon = new FitAddon.FitAddon(); term.loadAddon(fitAddon);
                term.open(document.getElementById('{self.term_id}'));
                term.write('\\x1b[32m[Local] Terminal Ready. Connecting...\\x1b[0m\\r\\n');
                setTimeout(() => {{ fitAddon.fit(); }}, 200);
                window.{self.term_id} = term; term.focus();
                term.onData(data => {{ emitEvent('term_input_{self.term_id}', data); }});
                new ResizeObserver(() => fitAddon.fit()).observe(document.getElementById('{self.term_id}'));
            }} catch(e) {{ console.error(e); }}
            """
            ui.run_javascript(init_js)
            ui.on(f'term_input_{self.term_id}', lambda e: self._write_to_ssh(e.args))

            # 3. 建立连接
            self.client, msg = await run.io_bound(get_ssh_client_sync, self.server_data)
            if not self.client:
                self._print_error(msg);
                return

            # 4. 启动 Shell
            self.channel = self.client.invoke_shell(term='xterm', width=100, height=30)
            self.channel.settimeout(0.0)
            self.active = True
            asyncio.create_task(self._read_loop())

    def _print_error(self, msg):
        b64_msg = base64.b64encode(f"\r\n\x1b[31m[Error] {str(msg)}\x1b[0m\r\n".encode()).decode()
        ui.run_javascript(f'if(window.{self.term_id}) window.{self.term_id}.write(atob("{b64_msg}"));')

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
                        var bytes = new Uint8Array(atob("{b64_data}").split("").map(function(c) {{ return c.charCodeAt(0); }}));
                        var decoded = new TextDecoder("utf-8").decode(bytes);
                        window.{self.term_id}.write(decoded);
                    }}"""
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