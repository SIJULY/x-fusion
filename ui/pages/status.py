# ui/pages/status.py
from nicegui import ui
from core.state import SERVERS_CACHE, PROBE_DATA_CACHE
from ui.common import format_bytes, is_mobile_device


async def status_page_router(request):
    # 强制深色背景
    ui.add_head_html('<style>body { background-color: #0f172a; color: white; margin: 0; }</style>')

    with ui.column().classes('w-full min-h-screen p-6'):
        # 顶部标题栏 (还原截图的灰色胶囊条)
        with ui.row().classes('w-full justify-center mb-10'):
            ui.label('X-Fusion Status').classes('text-3xl font-bold tracking-wider')

        # 网格布局
        with ui.grid().classes('w-full gap-4').style('grid-template-columns: repeat(auto-fill, minmax(300px, 1fr))'):
            for s in SERVERS_CACHE:
                status = PROBE_DATA_CACHE.get(s['url'], {})
                create_status_card(s, status)


def create_status_card(server, status):
    is_online = status.get('status') == 'online'

    # 离线样式：深红背景，红色边框
    # 在线样式：深灰背景
    bg_cls = 'bg-[#1e293b]' if is_online else 'bg-[#450a0a] border border-red-800'

    with ui.card().classes(f'w-full p-4 rounded-lg shadow-lg {bg_cls}'):
        # 头部：名称 + 图标
        with ui.row().classes('w-full justify-between items-start mb-4'):
            with ui.row().classes('items-center gap-2'):
                ui.label(server['name']).classes('font-bold text-lg text-white truncate')

            # 闪电图标
            icon_col = 'text-green-500' if is_online else 'text-red-500'
            ui.icon('bolt').classes(f'{icon_col} text-xl')

        if not is_online:
            ui.label('OFFLINE').classes('text-red-400 font-bold text-sm tracking-widest mt-2')
        else:
            # 进度条展示 (CPU / RAM)
            cpu = status.get('cpu_usage', 0)
            mem = status.get('mem_usage', 0)

            with ui.column().classes('w-full gap-3'):
                # CPU
                with ui.column().classes('w-full gap-1'):
                    with ui.row().classes('w-full justify-between text-xs text-gray-400'):
                        ui.label('CPU')
                        ui.label(f'{cpu}%')
                    ui.linear_progress(cpu / 100).props('color=blue track-color=grey-8').classes('h-1.5 rounded-full')

                # RAM
                with ui.column().classes('w-full gap-1'):
                    with ui.row().classes('w-full justify-between text-xs text-gray-400'):
                        ui.label('RAM')
                        ui.label(f'{int(mem)}%')
                    ui.linear_progress(mem / 100).props('color=purple track-color=grey-8').classes('h-1.5 rounded-full')