from nicegui import ui
import asyncio
from app.core.state import (
    SERVERS_CACHE, ADMIN_CONFIG, EXPANDED_GROUPS,
    SIDEBAR_UI_REFS, NODES_DATA
)
from app.utils.geo_ip import detect_country_group
from app.core.data_manager import save_admin_config
from app.services.server_ops import smart_sort_key


# 引入弹窗组件 (延迟导入放在函数内避免循环)
# from app.ui.components.dialogs import open_quick_group_create_dialog, open_server_dialog

@ui.refreshable
def render_sidebar_content():
    # 延迟导入，解决循环依赖
    from app.ui.pages.main import refresh_content
    from app.ui.components.dialogs import (
        open_quick_group_create_dialog,
        open_server_dialog,
        open_server_detail_dialog,
        open_combined_group_management,
        open_bulk_edit_dialog,
        batch_ssh_manager,
        open_cloudflare_settings_dialog,
        open_global_settings_dialog,
        open_data_mgmt_dialog
    )

    SIDEBAR_UI_REFS['groups'].clear()
    SIDEBAR_UI_REFS['rows'].clear()

    # --- 1. 顶部固定区域 ---
    btn_style = 'w-full bg-white border border-gray-200 rounded-lg shadow-sm text-gray-600 font-medium px-3 py-2 hover:shadow-md hover:-translate-y-0.5 transition-all'
    with ui.column().classes('w-full p-4 border-b bg-gray-50 flex-shrink-0 relative overflow-hidden'):
        ui.label('X-Fusion').classes(
            'absolute top-2 right-6 text-[3rem] font-black text-gray-200 opacity-30 pointer-events-none -rotate-12 select-none')
        ui.label('Panel v3.0').classes(
            'text-2xl font-black mb-4 z-10 bg-clip-text text-transparent bg-gradient-to-r from-gray-700 to-black')

        # 导航按钮 (假设 load_dashboard_stats 等也在 main 或 dashboard 模块)
        from app.ui.pages.dashboard import load_dashboard_stats
        from app.ui.pages.probe import render_probe_page
        from app.ui.pages.subs import load_subs_view

        with ui.column().classes('w-full gap-2 z-10'):
            ui.button('仪表盘', icon='dashboard', on_click=lambda: asyncio.create_task(load_dashboard_stats())).props(
                'flat align=left').classes(btn_style)
            ui.button('探针设置', icon='tune', on_click=render_probe_page).props('flat align=left').classes(btn_style)
            ui.button('订阅管理', icon='rss_feed', on_click=load_subs_view).props('flat align=left').classes(btn_style)

    # --- 2. 列表区域 ---
    with ui.column().props('id=sidebar-scroll-box').classes('w-full flex-grow overflow-y-auto p-2 gap-2 bg-slate-50'):
        # 功能按钮
        with ui.row().classes('w-full gap-2 px-1 mb-2'):
            ui.button('新建分组', icon='create_new_folder', on_click=open_quick_group_create_dialog).props(
                'dense unelevated').classes('flex-grow bg-blue-500 text-white rounded-lg')
            ui.button('添加服务器', icon='add', color='green', on_click=lambda: open_server_dialog(None)).props(
                'dense unelevated').classes('flex-grow bg-green-500 text-white rounded-lg')

        # A. 所有服务器
        with ui.row().classes(
                'w-full items-center justify-between p-3 border border-gray-200 rounded-xl mb-1 bg-white shadow-sm cursor-pointer hover:shadow-md transition-all').on(
                'click', lambda _: refresh_content('ALL')):
            with ui.row().classes('items-center gap-3'):
                ui.icon('dns', color='grey-8').classes('p-1.5 bg-gray-100 rounded-lg')
                ui.label('所有服务器').classes('font-bold text-gray-700')
            ui.badge(str(len(SERVERS_CACHE)), color='blue').props('rounded outline')

        # B. 自定义分组
        custom_tags = ADMIN_CONFIG.get('custom_groups', [])
        if custom_tags:
            ui.label('自定义分组').classes('text-xs font-bold text-gray-400 mt-4 mb-2 px-2 uppercase tracking-wider')
            for tag in custom_tags:
                servers = [s for s in SERVERS_CACHE if tag in s.get('tags', []) or s.get('group') == tag]
                is_open = tag in EXPANDED_GROUPS

                with ui.expansion(tag, icon='folder', value=is_open).classes(
                        'w-full border border-gray-200 rounded-xl mb-2 bg-white shadow-sm').on_value_change(
                        lambda e, g=tag: EXPANDED_GROUPS.add(g) if e.value else EXPANDED_GROUPS.discard(g)):
                    # 分组头点击 -> 刷新右侧
                    # 具体的 Header 渲染比较复杂，这里简化，实际需要用 Slot 覆盖来实现点击 header 刷新
                    with ui.column().classes('w-full gap-2 p-2 bg-gray-50/50') as col:
                        SIDEBAR_UI_REFS['groups'][tag] = col
                        if not servers:
                            ui.label('空分组').classes('text-xs text-gray-400 ml-4')
                        else:
                            for s in servers: render_single_sidebar_row(s, refresh_content, open_server_dialog)

                        # 管理按钮
                        ui.button('管理此分组', icon='settings',
                                  on_click=lambda _, g=tag: open_combined_group_management(g)).props(
                            'flat dense size=sm color=grey').classes('w-full mt-1')

        # C. 区域分组
        ui.label('区域分组').classes('text-xs font-bold text-gray-400 mt-4 mb-2 px-2 uppercase tracking-wider')
        country_buckets = {}
        for s in SERVERS_CACHE:
            c_group = detect_country_group(s.get('name', ''), s) or '🏳️ 其他地区'
            if c_group not in country_buckets: country_buckets[c_group] = []
            country_buckets[c_group].append(s)

        # 排序
        saved_order = ADMIN_CONFIG.get('group_order', [])

        def sort_key(k):
            return saved_order.index(k) if k in saved_order else 999

        for c_name in sorted(country_buckets.keys(), key=sort_key):
            c_servers = country_buckets[c_name]
            is_open = c_name in EXPANDED_GROUPS

            with ui.expansion('', icon=None, value=is_open).classes(
                    'w-full border border-gray-200 rounded-xl bg-white shadow-sm').props(
                    'expand-icon-toggle').on_value_change(
                    lambda e, g=c_name: EXPANDED_GROUPS.add(g) if e.value else EXPANDED_GROUPS.discard(g)) as exp:
                with exp.add_slot('header'):
                    with ui.row().classes('w-full h-full items-center justify-between py-2 cursor-pointer').on('click',
                                                                                                               lambda _,
                                                                                                                      g=c_name: refresh_content(
                                                                                                                       'COUNTRY',
                                                                                                                       g)):
                        with ui.row().classes('items-center gap-2'):
                            flag = c_name.split(' ')[0] if ' ' in c_name else '🏳️'
                            ui.label(flag).classes('text-lg')
                            ui.label(c_name.split(' ')[1] if ' ' in c_name else c_name).classes(
                                'font-bold text-gray-700 truncate')
                        ui.badge(str(len(c_servers)), color='green').props('rounded outline')

                with ui.column().classes('w-full gap-2 p-2 bg-slate-50/80 border-t border-gray-100') as col:
                    SIDEBAR_UI_REFS['groups'][c_name] = col
                    for s in c_servers: render_single_sidebar_row(s, refresh_content, open_server_dialog)

    # --- 3. 底部按钮 ---
    with ui.column().classes('w-full p-2 border-t mt-auto mb-4 gap-2 bg-white z-10 shadow-up'):
        btn_cls = 'w-full text-gray-600 text-xs font-bold bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 hover:bg-white hover:shadow-md transition-all'
        ui.button('批量 SSH', icon='playlist_play', on_click=batch_ssh_manager.open_dialog).props(
            'flat align=left').classes(btn_cls)
        ui.button('Cloudflare', icon='cloud', on_click=open_cloudflare_settings_dialog).props(
            'flat align=left').classes(btn_cls)
        ui.button('SSH 全局密钥', icon='vpn_key', on_click=open_global_settings_dialog).props(
            'flat align=left').classes(btn_cls)
        ui.button('备份 / 恢复', icon='save', on_click=open_data_mgmt_dialog).props('flat align=left').classes(btn_cls)


def render_single_sidebar_row(s, refresh_cb, settings_cb):
    """渲染单行服务器 (提取出来以便复用)"""
    btn_base = 'bg-white border-t border-x border-gray-200 border-b-[3px] border-b-gray-300 rounded-lg transition-all active:border-b-0 active:translate-y-[3px]'

    with ui.row().classes('w-full gap-2 no-wrap items-stretch') as row:
        ui.button(on_click=lambda _, s=s: refresh_cb('SINGLE', s)) \
            .bind_text_from(s, 'name') \
            .props('no-caps align=left flat text-color=grey-8') \
            .classes(f'{btn_base} flex-grow text-xs font-bold truncate px-3 py-2.5 hover:bg-gray-50')

        ui.button(icon='settings', on_click=lambda _, s=s: settings_cb(SERVERS_CACHE.index(s))) \
            .props('flat square size=sm text-color=grey-5') \
            .classes(f'{btn_base} w-10 py-2.5 px-0 hover:text-gray-700')

    SIDEBAR_UI_REFS['rows'][s['url']] = row
    return row