from nicegui import ui
from app.core.state import SUBS_CACHE, SERVERS_CACHE, NODES_DATA
from app.core.data_manager import save_subs
from app.utils.common import safe_copy_to_clipboard


async def load_subs_view():
    container = ui.context.client.layout.content_container
    container.clear()

    # 获取 Origin
    try:
        origin = await ui.run_javascript('return window.location.origin')
    except:
        origin = ""

    with container:
        with ui.row().classes('w-full justify-between items-center p-6'):
            ui.label('订阅管理').classes('text-2xl font-bold')
            # 新建逻辑省略，使用弹窗组件
            ui.button('新建订阅', icon='add', color='green')

        with ui.column().classes('w-full px-6 gap-4'):
            if not SUBS_CACHE:
                ui.label('暂无订阅').classes('text-gray-400 italic')

            for idx, sub in enumerate(SUBS_CACHE):
                with ui.card().classes('w-full p-4 border-l-4 border-blue-500'):
                    with ui.row().classes('justify-between w-full'):
                        ui.label(sub.get('name', '未命名')).classes('font-bold text-lg')

                        async def delete(i=idx):
                            SUBS_CACHE.pop(i)
                            await save_subs()
                            await load_subs_view()

                        ui.button(icon='delete', color='red', on_click=delete).props('flat dense')

                    link = f"{origin}/sub/{sub.get('token')}"
                    with ui.row().classes('w-full bg-gray-50 p-2 rounded mt-2 items-center gap-2'):
                        ui.label(link).classes('text-xs font-mono text-gray-600 flex-grow truncate')
                        ui.button(icon='content_copy', on_click=lambda u=link: safe_copy_to_clipboard(u)).props(
                            'flat round dense')