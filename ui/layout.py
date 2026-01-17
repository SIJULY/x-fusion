# ui/layout.py
import asyncio
from nicegui import ui, app

from core.state import SERVERS_CACHE, ADMIN_CONFIG, EXPANDED_GROUPS, SIDEBAR_UI_REFS, CURRENT_VIEW_STATE
from core.storage import save_admin_config
from core.config import AUTO_REGISTER_SECRET
from services.geoip import detect_country_group
from ui.common import safe_copy_to_clipboard, set_main_content_container
from ui.dialogs.server_edit import open_server_dialog  # 将在下一阶段创建
from ui.dialogs.settings import open_quick_group_create_dialog, open_combined_group_management  # 下一阶段创建


# 延迟导入 Router 以避免循环依赖
def get_router():
    from ui.pages import router
    return router


# ================= 侧边栏单行渲染 =================
def render_single_sidebar_row(s):
    btn_base = 'bg-white border-t border-x border-gray-200 border-b-[3px] border-b-gray-300 rounded-lg transition-all duration-100 active:border-b-0 active:border-t-[3px] active:translate-y-[3px]'
    btn_name_cls = f'{btn_base} flex-grow text-xs font-bold text-gray-700 truncate px-3 py-2.5 hover:bg-gray-50 hover:text-black hover:border-gray-400'
    btn_set_cls = f'{btn_base} w-10 py-2.5 px-0 flex items-center justify-center text-gray-400 hover:text-gray-700 hover:bg-gray-50 hover:border-gray-400'

    with ui.row().classes('w-full gap-2 no-wrap items-stretch') as row:
        # 点击名字 -> 跳转到单服务器视图
        async def on_click_server():
            # 简单的防抖：如果是当前机器，就不刷新
            if CURRENT_VIEW_STATE['scope'] == 'SINGLE' and CURRENT_VIEW_STATE['data'] == s:
                return
            await get_router().route_to('SINGLE', s)

        ui.button(on_click=on_click_server).bind_text_from(s, 'name').props(
            'no-caps align=left flat text-color=grey-8').classes(btn_name_cls)

        # 点击设置 -> 打开编辑弹窗
        ui.button(icon='settings', on_click=lambda _, s=s: open_server_dialog(SERVERS_CACHE.index(s))) \
            .props('flat square size=sm text-color=grey-5').classes(btn_set_cls).tooltip('配置 / 删除')

    # 注册引用
    SIDEBAR_UI_REFS['rows'][s['url']] = row
    return row


# ================= 侧边栏整体渲染 =================
@ui.refreshable
def render_sidebar_content():
    # 清空引用
    SIDEBAR_UI_REFS['groups'].clear()
    SIDEBAR_UI_REFS['rows'].clear()

    router = get_router()

    # --- 1. 顶部固定区 ---
    btn_style = 'w-full bg-white border border-gray-200 rounded-lg shadow-sm text-gray-600 font-medium px-3 py-2 transition-all hover:shadow-md hover:-translate-y-0.5 hover:text-gray-900'
    with ui.column().classes('w-full p-4 border-b bg-gray-50 flex-shrink-0 relative overflow-hidden'):
        ui.label('X-Fusion').classes(
            'absolute top-2 right-6 text-[3rem] font-black text-gray-200 opacity-30 -rotate-12 select-none')
        ui.label('小龙女她爸').classes(
            'text-2xl font-black mb-4 z-10 bg-gradient-to-r from-gray-700 to-black bg-clip-text text-transparent')

        with ui.column().classes('w-full gap-2 z-10'):
            ui.button('仪表盘', icon='dashboard', on_click=lambda: router.route_to('DASHBOARD')).props(
                'flat align=left').classes(btn_style)
            ui.button('探针设置', icon='tune', on_click=lambda: router.route_to('PROBE')).props(
                'flat align=left').classes(btn_style)
            ui.button('订阅管理', icon='rss_feed', on_click=lambda: router.route_to('SUBS')).props(
                'flat align=left').classes(btn_style)

    # --- 2. 列表滚动区 ---
    with ui.column().props('id=sidebar-scroll-box').classes('w-full flex-grow overflow-y-auto p-2 gap-2 bg-slate-50'):
        # 新建按钮
        with ui.row().classes('w-full gap-2 px-1 mb-2'):
            base_cls = 'flex-grow text-xs font-bold text-white rounded-lg border-b-4 active:border-b-0 active:translate-y-[4px] transition-all'
            ui.button('新建分组', icon='create_new_folder', on_click=open_quick_group_create_dialog).props(
                'dense unelevated').classes(f'bg-blue-500 border-blue-700 hover:bg-blue-400 {base_cls}')
            ui.button('添加服务器', icon='add', color='green', on_click=lambda: open_server_dialog(None)).props(
                'dense unelevated').classes(f'bg-green-500 border-green-700 hover:bg-green-400 {base_cls}')

        # A. 所有服务器
        with ui.row().classes(
                'w-full items-center justify-between p-3 border border-gray-200 rounded-xl mb-1 bg-white shadow-sm cursor-pointer hover:shadow-md').on(
                'click', lambda: router.route_to('ALL')):
            with ui.row().classes('items-center gap-3'):
                ui.icon('dns', color='grey-8').classes('text-sm p-1.5 bg-gray-100 rounded-lg')
                ui.label('所有服务器').classes('font-bold text-gray-700')
            ui.badge(str(len(SERVERS_CACHE)), color='blue').props('rounded outline')

        # B. 自定义分组
        tags = ADMIN_CONFIG.get('custom_groups', [])
        if tags:
            ui.label('自定义分组').classes('text-xs font-bold text-gray-400 mt-4 mb-2 px-2 uppercase tracking-wider')
            for tag in tags:
                srvs = [s for s in SERVERS_CACHE if tag in s.get('tags', []) or s.get('group') == tag]
                is_open = tag in EXPANDED_GROUPS

                with ui.expansion('', icon=None, value=is_open).classes(
                        'w-full border border-gray-200 rounded-xl mb-2 bg-white shadow-sm').on_value_change(
                        lambda e, g=tag: EXPANDED_GROUPS.add(g) if e.value else EXPANDED_GROUPS.discard(g)) as exp:
                    with exp.add_slot('header'):
                        with ui.row().classes('w-full h-full items-center justify-between cursor-pointer py-1').on(
                                'click', lambda _, g=tag: router.route_to('TAG', g)):
                            with ui.row().classes('items-center gap-3 flex-grow'):
                                ui.icon('folder', color='primary').classes('opacity-70')
                                ui.label(tag).classes('font-bold text-gray-700 truncate')
                            with ui.row().classes('items-center gap-2 pr-2').on('click.stop'):
                                ui.button(icon='settings',
                                          on_click=lambda _, g=tag: open_combined_group_management(g)).props(
                                    'flat dense round size=xs color=grey-4')
                                ui.badge(str(len(srvs)), color='orange' if not srvs else 'grey').props(
                                    'rounded outline')

                    with ui.column().classes('w-full gap-2 p-2 bg-gray-50/50') as col:
                        SIDEBAR_UI_REFS['groups'][tag] = col
                        for s in srvs: render_single_sidebar_row(s)

        # C. 区域分组
        ui.label('区域分组').classes('text-xs font-bold text-gray-400 mt-4 mb-2 px-2 uppercase tracking-wider')
        buckets = {}
        for s in SERVERS_CACHE:
            grp = detect_country_group(s.get('name', ''), s)
            if grp in ['默认分组', '自动注册', '', None]: grp = '🏳️ 其他地区'
            buckets.setdefault(grp, []).append(s)

        # 排序
        saved_order = ADMIN_CONFIG.get('group_order', [])
        sorted_regions = sorted(buckets.keys(), key=lambda x: saved_order.index(x) if x in saved_order else 999)

        for region in sorted_regions:
            srvs = buckets[region]
            is_open = region in EXPANDED_GROUPS

            with ui.expansion('', icon=None, value=is_open).classes(
                    'w-full border border-gray-200 rounded-xl bg-white shadow-sm').on_value_change(
                    lambda e, g=region: EXPANDED_GROUPS.add(g) if e.value else EXPANDED_GROUPS.discard(g)) as exp:
                with exp.add_slot('header'):
                    with ui.row().classes('w-full h-full items-center justify-between cursor-pointer py-2').on('click',
                                                                                                               lambda _,
                                                                                                                      g=region: router.route_to(
                                                                                                                       'COUNTRY',
                                                                                                                       g)):
                        with ui.row().classes('items-center gap-3 flex-grow'):
                            flag = region.split(' ')[0] if ' ' in region else '🏳️'
                            name = region.split(' ')[1] if ' ' in region else region
                            ui.label(flag).classes('text-lg')
                            ui.label(name).classes('font-bold text-gray-700 truncate')
                        ui.badge(str(len(srvs)), color='green').props('rounded outline').classes('mr-2')

                with ui.column().classes('w-full gap-2 p-2 bg-slate-50/80 border-t border-gray-100') as col:
                    SIDEBAR_UI_REFS['groups'][region] = col
                    for s in srvs: render_single_sidebar_row(s)

    # --- 3. 底部功能区 ---
    with ui.column().classes('w-full p-2 border-t mt-auto mb-4 gap-2 bg-white z-10'):
        btn_style = 'w-full text-gray-600 text-xs font-bold bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 hover:bg-white hover:shadow-md'

        # 这些弹窗函数将在 dialogs 模块实现
        from ui.dialogs.ssh_terminal import batch_ssh_manager
        from ui.dialogs.settings import open_cloudflare_settings_dialog, open_global_settings_dialog, \
            open_data_mgmt_dialog

        ui.button('批量 SSH 执行', icon='playlist_play', on_click=batch_ssh_manager.open_dialog).props(
            'flat align=left').classes(btn_style)
        ui.button('Cloudflare 设置', icon='cloud', on_click=open_cloudflare_settings_dialog).props(
            'flat align=left').classes(btn_style)
        ui.button('全局 SSH 设置', icon='vpn_key', on_click=open_global_settings_dialog).props(
            'flat align=left').classes(btn_style)
        ui.button('数据备份 / 恢复', icon='save', on_click=open_data_mgmt_dialog).props('flat align=left').classes(
            btn_style)


# ================= 页面初始化 (构建 Drawer + Header) =================
def init_layout(client_ip="Unknown"):
    # 1. 侧边栏 Drawer
    with ui.left_drawer(value=True, fixed=True).classes('bg-gray-50 border-r').props('width=400 bordered') as drawer:
        render_sidebar_content()

    # 2. 顶部 Header
    with ui.header().classes('bg-slate-900 text-white h-14 shadow-md'):
        with ui.row().classes('w-full items-center justify-between'):
            # 左侧
            with ui.row().classes('items-center gap-2'):
                ui.button(icon='menu', on_click=lambda: drawer.toggle()).props('flat round dense color=white')
                ui.label('X-Fusion Panel').classes('text-lg font-bold ml-2 tracking-wide')
                ui.label(f"[{client_ip}]").classes('text-xs text-gray-400 font-mono pt-1 hidden sm:block')

            # 右侧
            with ui.row().classes('items-center gap-2 mr-2'):
                # 重置密钥按钮
                async def reset_session():
                    new_ver = str(import_uuid.uuid4())[:8]  # 需 import uuid
                    ADMIN_CONFIG['session_version'] = new_ver
                    await save_admin_config()
                    ui.notify('密钥已重置，全员强制下线', type='warning')
                    await asyncio.sleep(1)
                    ui.navigate.to('/login')

                import uuid as import_uuid  # 临时引入
                ui.button(icon='gpp_bad', color='red', on_click=reset_session).props('flat dense round').tooltip(
                    '强制下线所有用户')

                ui.button(icon='vpn_key', on_click=lambda: safe_copy_to_clipboard(AUTO_REGISTER_SECRET)).props(
                    'flat dense round').tooltip('复制通讯密钥')
                ui.button(icon='logout', on_click=lambda: (app.storage.user.clear(), ui.navigate.to('/login'))).props(
                    'flat round dense').tooltip('退出')

    # 3. 主内容容器
    content = ui.column().classes('w-full h-full pl-4 pr-4 pt-4 overflow-y-auto bg-slate-50')
    set_main_content_container(content)
    return content