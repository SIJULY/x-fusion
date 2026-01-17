from nicegui import ui, app
from app.core.config import ADMIN_USER, ADMIN_PASS
from app.core.state import CURRENT_VIEW_STATE, SERVERS_CACHE
from app.ui.components.sidebar import render_sidebar_content
from app.ui.components.dialogs import open_server_detail_dialog


# 延迟导入页面内容，防止循环依赖
async def load_content_by_scope(scope, data):
    from app.ui.pages.dashboard import load_dashboard_stats  # 内部导入
    from app.ui.pages.probe import render_probe_page
    from app.ui.pages.subs import load_subs_view
    from app.ui.components.dialogs import open_server_dialog

    container = ui.context.client.layout.content_container
    container.clear()

    if scope == 'DASHBOARD':
        await load_dashboard_stats()  # 注意：dashboard.py 里需要有一个名为 load_dashboard_stats 的函数用于后台渲染
    elif scope == 'PROBE':
        await render_probe_page()
    elif scope == 'SUBS':
        await load_subs_view()
    elif scope == 'SINGLE':
        # 打开单服务器详情弹窗
        open_server_detail_dialog(data)
    elif scope == 'ALL':
        with container:
            ui.label(f'所有服务器列表 ({len(SERVERS_CACHE)})').classes('text-2xl font-bold mb-4')
            with ui.grid().classes('grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4'):
                for s in SERVERS_CACHE:
                    with ui.card().classes('cursor-pointer hover:shadow-lg transition').on('click', lambda _,
                                                                                                           s=s: open_server_detail_dialog(
                            s)):
                        ui.label(s.get('name')).classes('font-bold')
                        ui.label(s.get('url')).classes('text-xs text-gray-400')


# ================= 全局刷新入口 =================
async def refresh_content(scope, data=None, force_refresh=False):
    """侧边栏调用的核心切换函数"""
    CURRENT_VIEW_STATE['scope'] = scope
    CURRENT_VIEW_STATE['data'] = data
    await load_content_by_scope(scope, data)


# ================= 主页面入口 =================
def main_page_entry():
    # 1. 认证检查
    if not app.storage.user.get('authenticated'):
        ui.navigate.to('/login')
        return

    # 2. 布局框架
    # 左侧抽屉
    with ui.left_drawer(value=True).classes('bg-gray-50 border-r') as drawer:
        render_sidebar_content()

    # 顶部导航
    with ui.header().classes('bg-slate-900 text-white h-14'):
        with ui.row().classes('w-full items-center justify-between'):
            with ui.row().classes('items-center'):
                ui.button(icon='menu', on_click=drawer.toggle).props('flat round dense color=white')
                ui.label('X-Fusion Panel').classes('text-lg font-bold ml-2')

            ui.button(icon='logout', on_click=lambda: (app.storage.user.clear(), ui.navigate.to('/login'))).props(
                'flat dense')

    # 3. 主内容区 (挂载到 Client Layout 上以便全局访问)
    # 使用 client.layout 自定义属性来存储容器引用，方便其他模块访问
    ui.context.client.layout.content_container = ui.column().classes('w-full h-full p-4 overflow-y-auto bg-slate-50')

    # 4. 默认加载页面
    ui.timer(0.1, lambda: refresh_content('DASHBOARD'), once=True)