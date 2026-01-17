# ui/dialogs/ssh_terminal.py
import uuid
import base64
import asyncio
import paramiko
from nicegui import ui, run
from core.state import SERVERS_CACHE, ADMIN_CONFIG
from core.storage import save_admin_config, save_servers
from services.ssh_manager import get_ssh_client_sync, get_ssh_client
from ui.common import safe_notify


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
            ui.element('div').props(f'id={self.term_id}').classes('w-full h-full bg-black p-1 overflow-hidden')

            # 初始化 xterm.js
            ui.run_javascript(f"""
                if (!window.Terminal) return;
                var term = new Terminal({{ cursorBlink: true, fontSize: 13, fontFamily: 'monospace', theme: {{ background: '#000000' }} }});
                var fitAddon = new FitAddon.FitAddon();
                term.loadAddon(fitAddon);
                term.open(document.getElementById('{self.term_id}'));
                term.write('\\x1b[32mConnecting...\\x1b[0m\\r\\n');
                fitAddon.fit();
                window.{self.term_id} = term;
                term.onData(d => emitEvent('term_input_{self.term_id}', d));
                new ResizeObserver(() => fitAddon.fit()).observe(document.getElementById('{self.term_id}'));
            """)

            ui.on(f'term_input_{self.term_id}', lambda e: self._write(e.args))

            try:
                self.client, msg = await run.io_bound(get_ssh_client_sync, self.server_data)
                if not self.client:
                    self._send_to_js(f"\r\n\x1b[31mError: {msg}\x1b[0m\r\n")
                    return

                self.channel = self.client.invoke_shell(term='xterm', width=100, height=30)
                self.channel.settimeout(0.0)
                self.active = True
                asyncio.create_task(self._read_loop())

            except Exception as e:
                self._send_to_js(f"\r\n\x1b[31mException: {e}\x1b[0m\r\n")

    def _write(self, data):
        if self.channel and self.active:
            try:
                self.channel.send(data)
            except:
                pass

    def _send_to_js(self, text):
        b64 = base64.b64encode(text.encode('utf-8')).decode('utf-8')
        ui.run_javascript(f"""
            if(window.{self.term_id}) {{
                var b = atob("{b64}");
                var u = new Uint8Array(b.length);
                for(var i=0; i<b.length; i++) u[i] = b.charCodeAt(i);
                window.{self.term_id}.write(new TextDecoder().decode(u));
            }}
        """)

    async def _read_loop(self):
        while self.active:
            try:
                if self.channel.recv_ready():
                    data = self.channel.recv(4096)
                    if not data: break
                    # 使用 decode('utf-8', 'ignore') 避免中文乱码导致崩溃
                    self._send_to_js(data.decode('utf-8', 'ignore'))
                await asyncio.sleep(0.01)
            except:
                await asyncio.sleep(0.1)

    def close(self):
        self.active = False
        if self.client: self.client.close()
        ui.run_javascript(f'if(window.{self.term_id}) window.{self.term_id}.dispose();')


# ================= 批量 SSH 执行器 =================
class BatchSSH:
    def __init__(self):
        self.selected = set()
        self.dialog = None

    def open_dialog(self):
        self.selected = set()
        with ui.dialog() as d, ui.card().classes('w-full max-w-4xl h-[80vh] flex flex-col p-0'):
            self.dialog = d
            with ui.row().classes('w-full p-4 bg-gray-50 border-b justify-between items-center'):
                ui.label('批量 SSH 执行').classes('text-lg font-bold')
                ui.button(icon='close', on_click=d.close).props('flat round dense')

            self.container = ui.column().classes('w-full flex-grow p-0')
            self._render_selection()
        d.open()

    def _render_selection(self):
        self.container.clear()
        with self.container:
            with ui.row().classes('w-full p-2 gap-2 bg-white border-b'):
                ui.button('全选', on_click=lambda: self._toggle_all(True)).props('flat dense color=primary')
                self.count_lbl = ui.label('已选: 0').classes('ml-auto mr-4 self-center font-bold')

            self.checks = {}
            with ui.scroll_area().classes('w-full flex-grow p-4'):
                with ui.column().classes('w-full gap-1'):
                    for s in SERVERS_CACHE:
                        with ui.row().classes('items-center p-2 border rounded'):
                            c = ui.checkbox(on_change=self._update_cnt).props('dense')
                            self.checks[s['url']] = c
                            ui.label(s['name']).classes('font-bold ml-2')
                            ui.label(s['url']).classes('text-xs text-gray-400')

            with ui.row().classes('w-full p-4 border-t bg-gray-50 justify-end'):
                ui.button('下一步', on_click=self._to_exec).classes('bg-black text-white')

    def _toggle_all(self, val):
        for c in self.checks.values(): c.value = val
        self._update_cnt()

    def _update_cnt(self):
        n = sum(1 for c in self.checks.values() if c.value)
        self.count_lbl.set_text(f'已选: {n}')

    def _to_exec(self):
        self.selected = {u for u, c in self.checks.items() if c.value}
        if not self.selected: return safe_notify('未选择服务器', 'warning')

        self.container.clear()
        with self.container:
            with ui.column().classes('w-full p-4 border-b bg-white gap-2'):
                ui.label(f'向 {len(self.selected)} 台服务器发送命令:').classes('font-bold')
                self.cmd = ui.textarea(placeholder='uptime').classes('w-full font-mono').props('outlined rows=3')
                with ui.row().classes('w-full justify-between'):
                    ui.button('返回', on_click=self._render_selection).props('flat')
                    ui.button('执行', on_click=self._run, icon='play_arrow').classes('bg-green-600 text-white')

            self.log = ui.log().classes('w-full flex-grow bg-black text-white p-4 font-mono text-xs')

    async def _run(self):
        cmd_str = self.cmd.value.strip()
        if not cmd_str: return
        self.log.push(f"🚀 批量执行: {cmd_str}\n" + "-" * 40)

        sem = asyncio.Semaphore(10)

        async def work(url):
            async with sem:
                srv = next((s for s in SERVERS_CACHE if s['url'] == url), None)
                if not srv: return
                self.log.push(f"⏳ [{srv['name']}] 连接中...")

                def _task():
                    client, msg = get_ssh_client(srv)
                    if not client: return False, msg
                    try:
                        _, stdout, stderr = client.exec_command(cmd_str, timeout=30)
                        return True, (stdout.read().decode().strip(), stderr.read().decode().strip())
                    except Exception as e:
                        return False, str(e)
                    finally:
                        client.close()

                ok, res = await run.io_bound(_task)
                if ok:
                    out, err = res
                    if out: self.log.push(f"✅ [{srv['name']}] OUT:\n{out}")
                    if err: self.log.push(f"⚠️ [{srv['name']}] ERR:\n{err}")
                else:
                    self.log.push(f"❌ [{srv['name']}] 失败: {res}")
                self.log.push("-" * 40)

        tasks = [work(u) for u in self.selected]
        await asyncio.gather(*tasks)
        self.log.push("🏁 执行完毕")


batch_ssh_manager = BatchSSH()