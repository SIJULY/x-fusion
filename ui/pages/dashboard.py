# ui/pages/dashboard.py
import asyncio
import time
from nicegui import ui

from core.state import (
    SERVERS_CACHE, NODES_DATA, PROBE_DATA_CACHE,
    ADMIN_CONFIG, CURRENT_VIEW_STATE, REFRESH_LOCKS,
    LAST_SYNC_MAP, DASHBOARD_REFS
)
from services.xui_api import fetch_inbounds_safe, get_manager
from services.geoip import detect_country_group
from services.jobs import calculate_dashboard_data
from services.ping import get_real_ip_display, bind_ip_label
from ui.common import get_main_content_container, safe_notify, safe_copy_to_clipboard, format_bytes
from ui.dialogs.deploy import open_deploy_xhttp_dialog, open_deploy_hysteria_dialog
from ui.dialogs.server_edit import open_server_dialog
from ui.dialogs.ssh_terminal import WebSSH
from api.sub_api import generate_node_link, generate_detail_config


# ================= 辅助：安全查找索引 =================
def find_server_index(target_server):
    """通过URL查找服务器索引，避免对象引用不同导致的 ValueError"""
    for i, s in enumerate(SERVERS_CACHE):
        if s.get('url') == target_server.get('url'):
            return i
    return None


# ================= 仪表盘主页 =================
async def load_dashboard_stats():
    container = get_main_content_container()
    if not container: return

    init_data = calculate_dashboard_data() or {
        "servers": "0/0", "nodes": "0", "traffic": "0 GB", "subs": "0",
        "bar_chart": {"names": [], "values": []}, "pie_chart": []
    }

    container.classes(remove='justify-center items-center overflow-hidden p-6',
                      add='overflow-y-auto p-4 pl-6 justify-start')

    with container:
        # JS 轮询
        ui.run_javascript("""
        if (window.dashInterval) clearInterval(window.dashInterval);
        window.dashInterval = setInterval(async () => {
            if (document.hidden) return;
            try {
                const res = await fetch('/api/dashboard/live_data');
                if (!res.ok) return;
                const data = await res.json();
                if (data.error) return;
                ['stat-servers', 'stat-nodes', 'stat-traffic', 'stat-subs'].forEach((id, i) => {
                    const el = document.getElementById(id);
                    if (el) el.innerText = data[['servers', 'nodes', 'traffic', 'subs'][i]];
                });
                const barDom = document.getElementById('chart-bar');
                if (barDom) {
                    const chart = echarts.getInstanceByDom(barDom);
                    if (chart) chart.setOption({ xAxis: { data: data.bar_chart.names }, series: [{ data: data.bar_chart.values }] });
                }
            } catch (e) {}
        }, 3000);
        """)

        ui.label('系统概览').classes('text-3xl font-bold mb-4 text-slate-800')

        # 统计卡片
        with ui.row().classes('w-full gap-4 mb-6 items-stretch'):
            def stat(key, did, tit, sub, ico, grad, val):
                with ui.card().classes(
                        f'flex-1 p-3 shadow border-none text-white {grad} rounded-xl relative overflow-hidden'):
                    ui.element('div').classes('absolute -right-4 -top-4 w-20 h-20 bg-white opacity-10 rounded-full')
                    with ui.row().classes('items-center justify-between w-full relative z-10'):
                        with ui.column().classes('gap-0'):
                            ui.label(tit).classes('opacity-90 text-[10px] font-bold uppercase tracking-wider')
                            DASHBOARD_REFS[key] = ui.label(val).props(f'id={did}').classes(
                                'text-2xl font-extrabold tracking-tight my-0.5')
                            ui.label(sub).classes('opacity-70 text-[10px] font-medium')
                        ui.icon(ico).classes('text-3xl opacity-80')

            stat('servers', 'stat-servers', '在线服务器', 'Online / Total', 'dns',
                 'bg-gradient-to-br from-blue-500 to-indigo-600', init_data['servers'])
            stat('nodes', 'stat-nodes', '节点总数', 'Active Nodes', 'hub',
                 'bg-gradient-to-br from-purple-500 to-pink-600', init_data['nodes'])
            stat('traffic', 'stat-traffic', '总流量消耗', 'Total Traffic', 'bolt',
                 'bg-gradient-to-br from-emerald-500 to-teal-600', init_data['traffic'])
            stat('subs', 'stat-subs', '订阅配置', 'Subscriptions', 'rss_feed',
                 'bg-gradient-to-br from-orange-400 to-red-500', init_data['subs'])

        # 图表
        with ui.row().classes('w-full gap-4 mb-6 flex-wrap xl:flex-nowrap items-stretch'):
            with ui.card().classes('w-full xl:w-2/3 p-4 shadow-md border-none rounded-xl bg-white flex flex-col'):
                ui.label('📊 服务器流量排行 (GB)').classes('text-base font-bold text-slate-700 mb-2')
                DASHBOARD_REFS['bar_chart'] = ui.echart({
                    'tooltip': {'trigger': 'axis'},
                    'grid': {'left': '2%', 'right': '3%', 'bottom': '2%', 'top': '10%', 'containLabel': True},
                    'xAxis': {'type': 'category', 'data': init_data['bar_chart']['names'],
                              'axisLabel': {'rotate': 30, 'color': '#64748b'}},
                    'yAxis': {'type': 'value', 'splitLine': {'lineStyle': {'type': 'dashed', 'color': '#f1f5f9'}}},
                    'series': [{'type': 'bar', 'data': init_data['bar_chart']['values'], 'barWidth': '40%',
                                'itemStyle': {'borderRadius': [3, 3, 0, 0], 'color': '#6366f1'}}]
                }).classes('w-full h-56').props('id=chart-bar')

            with ui.card().classes('w-full xl:w-1/3 p-4 shadow-md border-none rounded-xl bg-white flex flex-col'):
                ui.label('🌏 服务器分布').classes('text-base font-bold text-slate-700 mb-1')
                DASHBOARD_REFS['pie_chart'] = ui.echart({
                    'tooltip': {'trigger': 'item'},
                    'legend': {'bottom': '0%', 'icon': 'circle', 'textStyle': {'color': '#64748b'}},
                    'series': [
                        {'type': 'pie', 'radius': ['40%', '70%'], 'center': ['50%', '42%'], 'avoidLabelOverlap': False,
                         'itemStyle': {'borderRadius': 4, 'borderColor': '#fff', 'borderWidth': 1},
                         'label': {'show': False}, 'data': init_data['pie_chart']}]
                }).classes('w-full h-56').props('id=chart-pie')

        # 地图
        with ui.row().classes('w-full gap-6 mb-6'):
            with ui.card().classes('w-full p-0 shadow-md border-none rounded-xl bg-slate-900 overflow-hidden relative'):
                with ui.row().classes(
                        'w-full px-6 py-3 bg-slate-800/50 border-b border-gray-700 justify-between items-center z-10 relative'):
                    ui.label('全球节点实景 (Global View)').classes('text-base font-bold text-white')
                    DASHBOARD_REFS['map_info'] = ui.label('Live Rendering').classes('text-[10px] text-gray-400')
                from ui.assets import GLOBE_STRUCTURE, GLOBE_JS_LOGIC
                ui.html(GLOBE_STRUCTURE, sanitize=False).classes('w-full h-[650px] overflow-hidden')
                globe_data = [{'lat': s['lat'], 'lon': s['lon'], 'name': "📍"} for s in SERVERS_CACHE if 'lat' in s]
                import json
                ui.run_javascript(f'window.DASHBOARD_DATA = {json.dumps(globe_data)};')
                ui.run_javascript(GLOBE_JS_LOGIC)


# ================= 列表视图刷新 =================
PAGE_SIZE = 30
SYNC_COOLDOWN = 1800


async def refresh_content(scope='ALL', data=None, force_refresh=False, sync_name_action=False, page_num=1):
    container = get_main_content_container()
    if not container: return

    cache_key = f"{scope}::{data}::P{page_num}"
    now = time.time();
    last_sync = LAST_SYNC_MAP.get(cache_key, 0)

    targets = []
    if scope == 'ALL':
        targets = list(SERVERS_CACHE)
    elif scope == 'TAG':
        targets = [s for s in SERVERS_CACHE if data in s.get('tags', [])]
    elif scope == 'COUNTRY':
        for s in SERVERS_CACHE:
            grp = detect_country_group(s.get('name', ''), s)
            if grp == data: targets.append(s)
    elif scope == 'SINGLE':
        # 精确查找
        targets = [data] if find_server_index(data) is not None else []

    if scope == 'SINGLE':
        if targets:
            await render_single_server_view(targets[0])
        else:
            container.clear(); ui.label('服务器不存在或已删除')
        return

    total = len(targets);
    pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    if page_num > pages: page_num = 1
    start = (page_num - 1) * PAGE_SIZE;
    end = start + PAGE_SIZE
    current_page_servers = targets[start:end]

    if not force_refresh and (now - last_sync < SYNC_COOLDOWN):
        await _render_list_ui(scope, data, current_page_servers, page_num, pages, total)
        safe_notify(f"🕒 显示缓存 ({int((now - last_sync) / 60)}分前)", "ongoing", timeout=1500)
        return

    if cache_key in REFRESH_LOCKS:
        if force_refresh: safe_notify("正在更新中...", "warning")
        return

    await _render_list_ui(scope, data, current_page_servers, page_num, pages, total)

    if not current_page_servers: return
    REFRESH_LOCKS.add(cache_key)

    async def _bg():
        try:
            to_sync = [s for s in current_page_servers if not s.get('probe_installed')]
            if to_sync:
                safe_notify(f"同步 {len(to_sync)} 个 API 节点...", "ongoing")
                tasks = [fetch_inbounds_safe(s, True, sync_name_action) for s in to_sync]
                await asyncio.gather(*tasks, return_exceptions=True)
                await _render_list_ui(scope, data, current_page_servers, page_num, pages, total)
                LAST_SYNC_MAP[cache_key] = time.time()
                if force_refresh: safe_notify("同步完成", "positive")
        finally:
            REFRESH_LOCKS.discard(cache_key)

    asyncio.create_task(_bg())


# ================= 列表渲染实现 =================
async def _render_list_ui(scope, data, targets, page, pages, total):
    container = get_main_content_container()
    container.clear()
    container.classes(remove='justify-center items-center overflow-hidden p-6',
                      add='overflow-y-auto p-4 pl-6 justify-start')

    with container:
        title = f"🌍 所有服务器" if scope == 'ALL' else (f"🏷️ 分组: {data}" if scope == 'TAG' else f"🏳️ 区域: {data}")
        show_ping = (scope == 'COUNTRY')

        with ui.row().classes('items-center w-full mb-4 border-b pb-2 justify-between'):
            ui.label(f"{title} ({total})").classes('text-2xl font-bold')
            if targets:
                from ui.pages.router import route_to
                ui.button('同步当前页', icon='sync',
                          on_click=lambda: refresh_content(scope, data, True, page_num=page)).props(
                    'outline color=primary')

        if not targets: ui.label('列表为空').classes('text-gray-400'); return

        cols_ping = 'grid-template-columns: 2fr 2fr 1.5fr 1.5fr 1fr 1fr 1.5fr'
        cols_no_ping = 'grid-template-columns: 2fr 2fr 1.5fr 1.5fr 1fr 1fr 0.5fr 1.5fr'
        css = cols_ping if show_ping else cols_no_ping

        with ui.element('div').classes(
                'grid w-full gap-4 font-bold text-gray-400 border-b pb-2 px-6 mb-1 uppercase text-xs').style(css):
            ui.label('服务器');
            ui.label('节点名称')
            ui.label('在线状态/IP' if show_ping else '所在组').classes('text-center')
            ui.label('流量').classes('text-center')
            ui.label('协议').classes('text-center');
            ui.label('端口').classes('text-center')
            if not show_ping: ui.label('状态').classes('text-center')
            ui.label('操作').classes('text-center')

        from ui.pages.router import route_to
        for srv in targets:
            nodes = (NODES_DATA.get(srv['url'], []) or []) + srv.get('custom_nodes', [])

            def draw_row(n=None, first=True):
                with ui.element('div').classes(
                        'grid w-full gap-4 py-3 px-4 items-center bg-white rounded-xl border border-gray-200 border-b-[3px] mb-2 hover:shadow-md transition').style(
                        css):
                    if first:
                        ui.label(srv['name']).classes('font-bold text-xs text-gray-700 truncate')
                    else:
                        ui.label('')

                    if not n:
                        ui.label('无节点').classes('text-xs text-gray-400 italic')
                        for _ in range(4 if not show_ping else 4): ui.label('--').classes('text-center text-gray-300')
                        with ui.row().classes('justify-center'):
                            ui.button(icon='settings', on_click=lambda _, s=srv: route_to('SINGLE', s)).props(
                                'flat dense size=sm round color=grey')
                        return

                    ui.label(n.get('remark', '')).classes('font-bold text-sm text-slate-700 truncate')

                    with ui.row().classes('justify-center'):
                        if show_ping:
                            is_on = srv.get('_status') == 'online'
                            ui.icon('bolt', color='green' if is_on else 'red').classes('text-sm')
                            ip = get_real_ip_display(srv['url'])
                            lbl = ui.label(ip).classes('text-[10px] bg-gray-100 px-1 rounded font-mono')
                            bind_ip_label(srv['url'], lbl)
                        else:
                            ui.label(srv.get('group', '')).classes(
                                'text-xs bg-gray-50 px-2 rounded-full font-bold text-gray-500')

                    t = format_bytes(n.get('up', 0) + n.get('down', 0))
                    ui.label(t).classes('text-center text-xs font-mono font-bold text-blue-600')
                    ui.label(str(n.get('protocol', '')).upper()).classes(
                        'text-center text-[10px] font-extrabold text-slate-500')
                    ui.label(str(n.get('port', ''))).classes('text-center text-xs font-mono font-bold text-slate-600')

                    if not show_ping:
                        with ui.row().classes('justify-center'):
                            c = 'green' if n.get('enable', True) else 'red'
                            ui.element('div').classes(f'w-2 h-2 rounded-full bg-{c}-500')

                    with ui.row().classes('justify-center gap-1'):
                        ui.button(icon='settings', on_click=lambda _, s=srv: route_to('SINGLE', s)).props(
                            'flat dense size=sm round color=grey')

            if not nodes:
                draw_row(None, True)
            else:
                for i, n in enumerate(nodes): draw_row(n, i == 0)

        if pages > 1:
            with ui.row().classes('w-full justify-center mt-4'):
                ui.pagination(1, pages, direction_links=True, value=page).props('dense flat').on_value_change(
                    lambda e: refresh_content(scope, data, False, False, e.value))


# ================= 单机详情页 (修复按钮点击) =================
async def render_single_server_view(server_conf):
    container = get_main_content_container()
    container.classes(remove='overflow-y-auto block', add='h-full overflow-hidden flex flex-col p-4')

    has_root = server_conf.get('probe_installed') and server_conf.get('ssh_host')
    has_api = bool(server_conf.get('user'))

    mgr = None
    if has_root or has_api:
        try:
            mgr = get_manager(server_conf)
        except:
            pass

    with container:
        # Header
        with ui.row().classes(
                'w-full justify-between items-center bg-white p-4 rounded-xl border border-gray-200 border-b-4 border-gray-300 shadow-sm flex-shrink-0'):
            with ui.row().classes('items-center gap-4'):
                ui.icon('dns', size='md').classes('text-slate-700 bg-slate-100 p-2 rounded-lg')
                with ui.column().classes('gap-0'):
                    ui.label(server_conf.get('name')).classes('text-xl font-black text-slate-800')
                    with ui.row().classes('items-center gap-2'):
                        ui.label(server_conf.get('url')).classes('text-xs font-mono text-gray-400')
                        if has_root: ui.badge('Root Mode', color='teal').props('rounded outline size=xs')
                        if has_api: ui.badge('API Mode', color='blue').props('rounded outline size=xs')

            btn_3d = 'text-xs font-bold text-white rounded-lg px-4 py-2 border-b-4 active:border-b-0 active:translate-y-[4px] transition-all'
            with ui.row().classes('gap-3'):
                async def reload():
                    await fetch_inbounds_safe(server_conf, True)
                    await render_single_server_view(server_conf)

                ui.button('部署 XHTTP', icon='rocket_launch',
                          on_click=lambda: open_deploy_xhttp_dialog(server_conf, reload)).props('unelevated').classes(
                    f'bg-blue-600 border-blue-800 {btn_3d}')
                ui.button('部署 Hy2', icon='bolt',
                          on_click=lambda: open_deploy_hysteria_dialog(server_conf, reload)).props(
                    'unelevated').classes(f'bg-purple-600 border-purple-800 {btn_3d}')

                # ✨✨✨ 修复：使用 find_server_index 查找真实索引 ✨✨✨
                def open_edit():
                    real_idx = find_server_index(server_conf)
                    if real_idx is not None:
                        open_server_dialog(real_idx)
                    else:
                        safe_notify("服务器定位失败", "warning")

                ui.button('配置', icon='edit', on_click=open_edit).props('outline').classes('rounded-lg px-4')

        # 节点列表
        with ui.card().classes(
                'w-full flex-grow flex flex-col p-0 rounded-xl border border-gray-200 border-b-[4px] border-b-gray-300 shadow-sm overflow-hidden mt-4'):
            cols_single = 'grid-template-columns: 3fr 1fr 1fr 1fr 1fr 1fr 1.5fr'
            with ui.element('div').classes(
                    'grid w-full gap-4 font-bold text-gray-400 border-b border-gray-200 pb-2 pt-3 px-4 text-xs uppercase bg-gray-50').style(
                    cols_single):
                ui.label('节点名称').classes('text-left')
                for t in ['类型', '流量', '协议', '端口', '状态', '操作']: ui.label(t).classes('text-center')

            with ui.scroll_area().classes('w-full flex-grow bg-gray-50 p-2'):
                nodes = (NODES_DATA.get(server_conf['url'], []) or []) + server_conf.get('custom_nodes', [])

                if not nodes: ui.label('暂无节点').classes('w-full text-center text-gray-400 mt-10')

                for n in nodes:
                    with ui.element('div').classes(
                            'grid w-full gap-4 py-3 px-4 items-center bg-white rounded-xl border border-gray-200 mb-2 shadow-sm transition hover:shadow-md hover:-translate-y-0.5').style(
                            cols_single):
                        ui.label(n.get('remark', '')).classes('font-bold text-sm text-slate-700 truncate')

                        is_cust = n.get('_is_custom')
                        tag_col = 'purple' if is_cust else ('teal' if has_root else 'blue')
                        tag_txt = 'Custom' if is_cust else ('Root' if has_root else 'API')
                        ui.label(tag_txt).classes(
                            f'text-[10px] bg-{tag_col}-100 text-{tag_col}-700 font-bold px-2 py-0.5 rounded-full mx-auto w-fit')

                        t = format_bytes(n.get('up', 0) + n.get('down', 0))
                        ui.label(t).classes('text-center text-xs font-mono font-bold text-gray-500')
                        ui.label(str(n.get('protocol', '')).upper()).classes(
                            'text-center text-[10px] font-extrabold text-slate-500')
                        ui.label(str(n.get('port', ''))).classes(
                            'text-center text-xs font-mono font-bold text-blue-600')

                        is_en = n.get('enable', True)
                        with ui.row().classes('justify-center items-center gap-1'):
                            c = 'green' if is_en else 'red'
                            ui.element('div').classes(f'w-2 h-2 rounded-full bg-{c}-500')
                            ui.label('ON' if is_en else 'OFF').classes(f'text-[10px] font-bold text-{c}-600')

                        with ui.row().classes('justify-center gap-2'):
                            async def cp(node=n):
                                link = node.get('_raw_link') or generate_node_link(node, server_conf['url'])
                                await safe_copy_to_clipboard(link)

                            ui.button(icon='content_copy', on_click=cp).props(
                                'flat dense size=sm round text-color=grey-7').tooltip('复制链接')

                            async def rm(node=n):
                                if is_cust:
                                    server_conf['custom_nodes'].remove(node)
                                    await save_nodes_cache()
                                    await render_single_server_view(server_conf)
                                    safe_notify('已删除 (Custom)', 'positive')
                                elif mgr:
                                    if hasattr(mgr, '_exec_remote_script'):
                                        await mgr.delete_inbound(node['id'])
                                    else:
                                        await run.io_bound(mgr.delete_inbound, node['id'])
                                    await reload()
                                    safe_notify('已删除 (Panel)', 'positive')

                            ui.button(icon='delete', color='red', on_click=rm).props('flat dense size=sm round')

        # SSH
        with ui.card().classes(
                'w-full h-[400px] flex-shrink-0 mt-4 p-0 rounded-xl border border-gray-300 bg-slate-900 overflow-hidden flex flex-col'):
            with ui.row().classes(
                    'w-full h-10 bg-slate-800 items-center px-4 border-b border-slate-700 justify-between'):
                ui.label('SSH Terminal').classes('text-white font-mono text-xs')
                ui.button(icon='terminal', color='green', on_click=lambda: open_ssh()).props(
                    'flat dense size=sm').tooltip('重新连接')

            term_box = ui.element('div').classes('w-full flex-grow bg-black relative')
            with term_box:
                cover = ui.column().classes('absolute inset-0 justify-center items-center bg-black/80 z-10')
                with cover:
                    ui.icon('terminal', size='4rem').classes('text-gray-700 mb-2')
                    btn_con = ui.button('连接终端', on_click=lambda: open_ssh()).classes(
                        'bg-blue-600 text-white font-bold')

            def open_ssh():
                term_box.clear()
                ssh = WebSSH(term_box, server_conf)
                asyncio.create_task(ssh.connect())