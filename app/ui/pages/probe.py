from nicegui import ui
from app.core.state import ADMIN_CONFIG
from app.core.data_manager import save_admin_config
from app.services.probe import batch_install_all_probes


async def render_probe_page():
    # 1. 确保配置初始化
    if not ADMIN_CONFIG.get('probe_enabled'):
        ADMIN_CONFIG['probe_enabled'] = True

    # 2. 清理容器 (假设由 main.py 提供的 content_container)
    # 注意：为了模块化，我们通常传入 container 或使用 ui.context
    # 这里为了简便，直接操作当前上下文
    ui.context.client.layout.content_container.clear()

    with ui.context.client.layout.content_container:
        with ui.column().classes('w-full max-w-5xl gap-6 p-6'):
            # 标题
            with ui.row().classes('items-center gap-3'):
                ui.icon('tune', color='primary').classes('text-2xl')
                ui.label('探针管理与设置').classes('text-2xl font-bold text-slate-800')

            with ui.grid().classes('w-full grid-cols-1 lg:grid-cols-2 gap-6'):
                # 卡片 1: 基础连接
                with ui.card().classes('w-full p-6'):
                    ui.label('📡 主控端地址').classes('text-lg font-bold mb-2')
                    url_input = ui.input(value=ADMIN_CONFIG.get('manager_base_url', ''),
                                         placeholder='http://1.2.3.4:8080').classes('w-full')

                    async def save_url():
                        ADMIN_CONFIG['manager_base_url'] = url_input.value.strip().rstrip('/')
                        await save_admin_config()
                        ui.notify('保存成功', type='positive')

                    ui.button('保存地址', on_click=save_url).classes('mt-4')

                # 卡片 2: 测速目标
                with ui.card().classes('w-full p-6'):
                    ui.label('🚀 三网 Ping 目标').classes('text-lg font-bold mb-2')
                    ct = ui.input('电信 IP', value=ADMIN_CONFIG.get('ping_target_ct', ''))
                    cu = ui.input('联通 IP', value=ADMIN_CONFIG.get('ping_target_cu', ''))
                    cm = ui.input('移动 IP', value=ADMIN_CONFIG.get('ping_target_cm', ''))

                    async def save_ping():
                        ADMIN_CONFIG['ping_target_ct'] = ct.value
                        ADMIN_CONFIG['ping_target_cu'] = cu.value
                        ADMIN_CONFIG['ping_target_cm'] = cm.value
                        await save_admin_config()
                        ui.notify('测速目标已保存 (需更新探针生效)', type='positive')

                    ui.button('保存目标', on_click=save_ping).classes('mt-4')

            # 底部操作栏
            with ui.card().classes('w-full p-6 bg-orange-50'):
                ui.label('批量操作').classes('text-lg font-bold text-orange-800 mb-2')
                ui.label('将重新连接所有服务器并更新探针脚本').classes('text-sm text-orange-600 mb-4')

                async def reinstall():
                    ui.notify('正在后台更新...', type='ongoing')
                    await batch_install_all_probes()
                    ui.notify('任务已完成', type='positive')

                ui.button('更新所有探针', icon='system_update_alt', on_click=reinstall).props('color=orange')