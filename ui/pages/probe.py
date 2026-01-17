# ui/pages/probe.py
from nicegui import ui
from core.state import ADMIN_CONFIG, SERVERS_CACHE
from core.storage import save_admin_config
from ui.common import get_main_content_container, safe_notify, safe_copy_to_clipboard
from services.ssh_manager import install_probe_on_server
from ui.dialogs.settings import open_combined_group_management, open_quick_group_create_dialog
from ui.dialogs.ssh_terminal import batch_ssh_manager
import asyncio


# 排序弹窗辅助
def open_group_sort_dialog():
    safe_notify("功能开发中...", "warning")


async def render_probe_page():
    container = get_main_content_container()
    container.clear()
    container.classes(remove='justify-center items-center', add='p-6 bg-slate-50 justify-start')

    with container:
        # 顶部标题栏
        with ui.row().classes('w-full items-center gap-3 mb-6'):
            with ui.element('div').classes('p-2 bg-blue-600 rounded-lg shadow-sm'):
                ui.icon('tune', color='white').classes('text-2xl')
            with ui.column().classes('gap-0'):
                ui.label('探针管理与设置').classes('text-xl font-extrabold text-slate-800 tracking-tight')
                ui.label('PROBE CONFIGURATION & MANAGEMENT').classes(
                    'text-[10px] font-bold text-gray-400 uppercase tracking-widest')

        # 核心布局: 左右分栏
        with ui.grid().classes('w-full grid-cols-1 lg:grid-cols-7 gap-6 items-stretch'):

            # === 左侧设置区 (4/7) ===
            with ui.column().classes('lg:col-span-4 w-full gap-6'):

                # 1. 基础连接设置
                with ui.card().classes('w-full p-6 bg-white border border-gray-200 shadow-sm rounded-xl'):
                    with ui.row().classes('items-center gap-2 mb-4 border-b border-gray-100 pb-2 w-full'):
                        ui.icon('hub', color='blue').classes('text-xl')
                        ui.label('基础连接设置').classes('text-lg font-bold text-slate-700')

                    ui.label('📡 主控端外部地址 (Agent 连接地址)').classes('text-sm font-bold text-gray-600')
                    url_input = ui.input(value=ADMIN_CONFIG.get('manager_base_url', '')).props(
                        'outlined dense').classes('w-full')
                    ui.label('Agent 将向此地址推送数据。请填写 http://公网IP:端口 或 https://域名').classes(
                        'text-xs text-gray-400 mb-2')

                    async def save_url():
                        ADMIN_CONFIG['manager_base_url'] = url_input.value.rstrip('/')
                        await save_admin_config()
                        safe_notify('✅ 连接设置已保存', 'positive')

                    ui.button('保存连接设置', icon='save', on_click=save_url).props('unelevated color=blue-7').classes(
                        'w-full mt-2 font-bold')

                # 2. Ping 测速目标
                with ui.card().classes('w-full p-6 bg-white border border-gray-200 shadow-sm rounded-xl'):
                    with ui.row().classes('items-center gap-2 mb-4 border-b border-gray-100 pb-2 w-full'):
                        ui.icon('speed', color='orange').classes('text-xl')
                        ui.label('三网延迟测速目标 (Ping)').classes('text-lg font-bold text-slate-700')

                    with ui.grid().classes('w-full grid-cols-1 sm:grid-cols-3 gap-4'):
                        with ui.column().classes('gap-1'):
                            ui.label('中国电信 IP').classes('text-xs font-bold text-gray-500')
                            ct = ui.input(value=ADMIN_CONFIG.get('ping_target_ct', '')).props('outlined dense').classes(
                                'w-full')
                        with ui.column().classes('gap-1'):
                            ui.label('中国联通 IP').classes('text-xs font-bold text-gray-500')
                            cu = ui.input(value=ADMIN_CONFIG.get('ping_target_cu', '')).props('outlined dense').classes(
                                'w-full')
                        with ui.column().classes('gap-1'):
                            ui.label('中国移动 IP').classes('text-xs font-bold text-gray-500')
                            cm = ui.input(value=ADMIN_CONFIG.get('ping_target_cm', '')).props('outlined dense').classes(
                                'w-full')

                    ui.label('ℹ️ 修改测速目标后，请点击右侧的“更新所有探针”按钮以生效。').classes(
                        'text-xs text-gray-400 mt-2')

                    async def save_ping():
                        ADMIN_CONFIG['ping_target_ct'] = ct.value
                        ADMIN_CONFIG['ping_target_cu'] = cu.value
                        ADMIN_CONFIG['ping_target_cm'] = cm.value
                        await save_admin_config()
                        safe_notify('✅ 测速目标已保存', 'positive')

                    ui.button('保存测速目标', icon='save', on_click=save_ping).props(
                        'unelevated color=orange-7').classes('w-full mt-2 font-bold')

                # 3. 通知设置 (Telegram)
                with ui.card().classes('w-full p-6 bg-white border border-gray-200 shadow-sm rounded-xl'):
                    with ui.row().classes('items-center gap-2 mb-4 border-b border-gray-100 pb-2 w-full'):
                        ui.icon('notifications', color='purple').classes('text-xl')
                        ui.label('通知设置 (Telegram)').classes('text-lg font-bold text-slate-700')

                    with ui.grid().classes('w-full grid-cols-1 sm:grid-cols-2 gap-4'):
                        with ui.column().classes('gap-1'):
                            ui.label('Bot Token').classes('text-xs font-bold text-gray-500')
                            tg_token = ui.input(value=ADMIN_CONFIG.get('tg_bot_token', '')).props(
                                'outlined dense').classes('w-full')
                        with ui.column().classes('gap-1'):
                            ui.label('Chat ID').classes('text-xs font-bold text-gray-500')
                            tg_id = ui.input(value=ADMIN_CONFIG.get('tg_chat_id', '')).props('outlined dense').classes(
                                'w-full')

                    ui.label('用于接收服务器离线/恢复的实时通知。').classes('text-xs text-gray-400 mt-2')

                    async def save_tg():
                        ADMIN_CONFIG['tg_bot_token'] = tg_token.value
                        ADMIN_CONFIG['tg_chat_id'] = tg_id.value
                        await save_admin_config()
                        safe_notify('✅ 通知配置已保存', 'positive')

                    ui.button('保存通知设置', icon='save', on_click=save_tg).props('unelevated color=purple-7').classes(
                        'w-full mt-2 font-bold')

            # === 右侧快捷区 (3/7) ===
            with ui.column().classes('lg:col-span-3 w-full gap-6'):

                # 1. 快捷操作
                with ui.card().classes('w-full p-6 bg-white border border-gray-200 shadow-sm rounded-xl'):
                    ui.label('快捷操作').classes(
                        'text-lg font-bold text-slate-700 mb-4 border-l-4 border-blue-500 pl-2')

                    with ui.column().classes('w-full gap-3'):
                        async def copy_cmd():
                            base = url_input.value or "http://YOUR_IP:8080"
                            token = ADMIN_CONFIG.get('probe_token', 'default')
                            cmd = f'curl -sL {base}/static/x-install.sh | bash -s -- "{token}" "{base}/api/probe/register"'
                            await safe_copy_to_clipboard(cmd)

                        ui.button('复制安装命令', icon='content_copy', on_click=copy_cmd).classes(
                            'w-full bg-blue-50 text-blue-700 border border-blue-200 shadow-sm hover:bg-blue-100 font-bold')

                        with ui.row().classes('w-full gap-2'):
                            ui.button('分组管理', icon='settings', on_click=open_quick_group_create_dialog).classes(
                                'flex-1 bg-blue-50 text-blue-700 border border-blue-200 shadow-sm hover:bg-blue-100 font-bold')
                            ui.button('排序', icon='sort', on_click=open_group_sort_dialog).classes(
                                'flex-1 bg-gray-50 text-gray-700 border border-gray-200 shadow-sm hover:bg-gray-100 font-bold')

                        async def update_all():
                            safe_notify('正在后台更新所有探针...', 'ongoing')
                            for s in SERVERS_CACHE:
                                if s.get('probe_installed'): asyncio.create_task(install_probe_on_server(s))

                        ui.button('更新所有探针', icon='system_update_alt', on_click=update_all).classes(
                            'w-full bg-orange-50 text-orange-700 border border-orange-200 shadow-sm hover:bg-orange-100 font-bold')

                # 2. 监控墙入口
                with ui.card().classes(
                        'w-full p-6 bg-gradient-to-br from-slate-800 to-slate-900 text-white rounded-xl shadow-lg relative overflow-hidden group cursor-pointer').on(
                        'click', lambda: ui.navigate.to('/status', new_tab=True)):
                    ui.icon('public', size='10rem').classes(
                        'absolute -right-8 -bottom-8 text-white opacity-10 group-hover:rotate-12 transition transform duration-500')
                    ui.label('公开监控墙').classes('text-xl font-bold mb-2')
                    ui.label('点击前往查看实时状态地图').classes('text-sm text-gray-400 mb-6')
                    with ui.row().classes(
                            'items-center gap-2 text-blue-400 font-bold text-base group-hover:gap-3 transition-all'):
                        ui.label('立即前往');
                        ui.icon('arrow_forward')

                # 3. 数据概览
                online = len([s for s in SERVERS_CACHE if s.get('_status') == 'online'])
                total = len(SERVERS_CACHE)
                probe_cnt = len([s for s in SERVERS_CACHE if s.get('probe_installed')])

                with ui.card().classes('w-full p-6 bg-white border border-gray-200 shadow-sm rounded-xl'):
                    ui.label('数据概览').classes(
                        'text-lg font-bold text-slate-700 mb-4 border-l-4 border-green-500 pl-2')

                    def row(label, val, col):
                        with ui.row().classes(
                                'w-full justify-between items-center border-b border-gray-50 pb-3 mb-3 last:border-0'):
                            ui.label(label).classes('text-gray-500 text-sm')
                            ui.label(str(val)).classes(f'font-bold text-xl {col}')

                    row('总服务器', total, 'text-slate-800')
                    row('探针在线', online, 'text-green-600')
                    row('已安装探针', probe_cnt, 'text-purple-600')