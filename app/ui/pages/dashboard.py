from nicegui import ui
from app.core.state import SERVERS_CACHE, NODES_DATA, SUBS_CACHE, DASHBOARD_REFS, CURRENT_VIEW_STATE
from app.utils.geo_ip import detect_country_group
from app.utils.common import format_bytes


# ================= 数据计算逻辑 (共用) =================
def calculate_dashboard_data():
    """计算面板统计数据 (后台专用)"""
    total_servers = len(SERVERS_CACHE)
    online_servers = 0
    total_nodes = 0
    total_traffic = 0

    server_traffic_map = {}
    from collections import Counter
    country_counter = Counter()

    for s in SERVERS_CACHE:
        # 统计区域
        g_name = s.get('group')
        if not g_name or '默认' in g_name:
            g_name = detect_country_group(s.get('name', ''), s)
        country_counter[g_name or '其他'] += 1

        # 统计节点与流量
        nodes = NODES_DATA.get(s['url'], [])
        custom = s.get('custom_nodes', [])

        if nodes:
            online_servers += 1
            total_nodes += len(nodes)
            for n in nodes:
                t = int(n.get('up', 0)) + int(n.get('down', 0))
                total_traffic += t

        if custom: total_nodes += len(custom)
        server_traffic_map[s.get('name', 'UNK')] = total_traffic

    # 图表数据
    sorted_traffic = sorted(server_traffic_map.items(), key=lambda x: x[1], reverse=True)[:10]
    bar_names = [x[0] for x in sorted_traffic]
    bar_values = [round(x[1] / (1024 ** 3), 2) for x in sorted_traffic]

    pie_data = [{'name': k, 'value': v} for k, v in country_counter.most_common(6)]

    return {
        "servers": f"{online_servers}/{total_servers}",
        "nodes": str(total_nodes),
        "traffic": format_bytes(total_traffic),
        "subs": str(len(SUBS_CACHE)),
        "bar_chart": {"names": bar_names, "values": bar_values},
        "pie_chart": pie_data
    }


# ================= 后台仪表盘渲染 =================
async def load_dashboard_stats():
    """
    渲染【后台内部】的仪表盘
    注意：这是嵌入在 main_page_entry 的 content_container 里的
    """
    # 1. 标记视图状态
    CURRENT_VIEW_STATE['scope'] = 'DASHBOARD'
    CURRENT_VIEW_STATE['data'] = None

    # 2. 获取容器 (必须存在)
    try:
        container = ui.context.client.layout.content_container
    except:
        return  # 如果不在UI上下文中，直接退出

    container.clear()

    # 3. 获取数据
    data = calculate_dashboard_data()

    with container:
        # 标题栏
        with ui.row().classes('items-center gap-2 mb-6'):
            ui.icon('dashboard', color='primary').classes('text-3xl')
            ui.label('系统概览 (Admin Dashboard)').classes('text-2xl font-bold text-slate-800')

        # === A. 顶部统计卡片 (4张卡) ===
        with ui.grid().classes('w-full grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-6'):
            def stat_card(key, title, val, icon, color):
                with ui.card().classes(f'p-4 border-l-4 border-{color}-500 shadow-sm'):
                    with ui.row().classes('justify-between items-center w-full'):
                        with ui.column().classes('gap-0'):
                            ui.label(title).classes('text-xs font-bold text-gray-400 uppercase')
                            # 保存引用到全局 DASHBOARD_REFS 以便后台刷新
                            DASHBOARD_REFS[key] = ui.label(val).classes(f'text-2xl font-black text-{color}-600')
                        ui.icon(icon).classes(f'text-4xl text-{color}-100')

            stat_card('servers', '在线服务器', data['servers'], 'dns', 'blue')
            stat_card('nodes', '总节点数', data['nodes'], 'hub', 'purple')
            stat_card('traffic', '流量消耗', data['traffic'], 'bolt', 'green')
            stat_card('subs', '订阅配置', data['subs'], 'rss_feed', 'orange')

        # === B. 简易图表区 (ECharts) ===
        ui.add_head_html('<script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>')

        with ui.grid().classes('w-full grid-cols-1 lg:grid-cols-3 gap-6'):
            # 1. 流量排行 (占 2/3)
            with ui.card().classes('col-span-1 lg:col-span-2 p-4 shadow-sm'):
                ui.label('📊 流量 Top 10 (GB)').classes('font-bold text-gray-700 mb-2')
                DASHBOARD_REFS['bar_chart'] = ui.echart({
                    'tooltip': {'trigger': 'axis'},
                    'grid': {'left': '3%', 'right': '4%', 'bottom': '3%', 'containLabel': True},
                    'xAxis': {'type': 'category', 'data': data['bar_chart']['names'],
                              'axisLabel': {'rotate': 30, 'color': '#666'}},
                    'yAxis': {'type': 'value'},
                    'series': [{'type': 'bar', 'data': data['bar_chart']['values'], 'itemStyle': {'color': '#3b82f6'},
                                'barWidth': '40%'}]
                }).classes('w-full h-64')

            # 2. 区域分布 (占 1/3)
            with ui.card().classes('col-span-1 p-4 shadow-sm'):
                ui.label('🌏 区域分布').classes('font-bold text-gray-700 mb-2')
                DASHBOARD_REFS['pie_chart'] = ui.echart({
                    'tooltip': {'trigger': 'item'},
                    'legend': {'bottom': '0%'},
                    'series': [{
                        'name': '分布', 'type': 'pie', 'radius': ['40%', '70%'],
                        'data': data['pie_chart'],
                        'label': {'show': False}
                    }]
                }).classes('w-full h-64')