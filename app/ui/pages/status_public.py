from nicegui import ui
from fastapi import Request
from app.core.state import SERVERS_CACHE, NODES_DATA, PROBE_DATA_CACHE
from app.utils.common import format_bytes
from app.ui.pages.dashboard import calculate_dashboard_data  # 复用计算逻辑


# ================= 手机端渲染 =================
async def render_mobile_status_page():
    data = calculate_dashboard_data()

    # 强制黑色背景
    with ui.column().classes('w-full min-h-screen bg-black text-white p-4 gap-4'):
        ui.label('X-Fusion Status').classes('text-xl font-black text-blue-500 mb-2')

        # 简单列表展示
        for s in SERVERS_CACHE:
            status = PROBE_DATA_CACHE.get(s['url'], {})
            is_online = status.get('status') == 'online'

            # 卡片
            with ui.card().classes('w-full bg-gray-900 border border-gray-800 p-3'):
                with ui.row().classes('justify-between items-center w-full'):
                    ui.label(s.get('name')).classes('font-bold text-white')
                    ui.icon('circle', color='green' if is_online else 'red').classes('text-xs')

                if is_online:
                    with ui.row().classes('gap-4 mt-2 text-xs text-gray-400'):
                        ui.label(f"CPU: {status.get('cpu_usage', 0)}%")
                        ui.label(f"MEM: {int(status.get('mem_usage', 0))}%")
                        up_speed = format_bytes(status.get('net_speed_out', 0))
                        ui.label(f"UP: {up_speed}/s")


# ================= 电脑端渲染 (大屏) =================
async def render_desktop_status_page():
    # 强制注入 ECharts
    ui.add_head_html('<script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>')

    data = calculate_dashboard_data()

    # 全屏容器 (Slate-900 背景)
    with ui.column().classes('w-full h-screen bg-slate-900 text-white p-0 overflow-hidden'):
        # 1. 顶部 Header
        with ui.row().classes(
                'w-full p-6 justify-between items-center bg-slate-800/50 backdrop-blur-md shadow-md border-b border-white/10'):
            with ui.row().classes('items-center gap-3'):
                ui.icon('public', color='blue').classes('text-3xl')
                ui.label('X-Fusion Monitor').classes('text-2xl font-black tracking-wider')

            with ui.row().classes('gap-6'):
                def head_stat(label, val, col):
                    with ui.column().classes('items-end gap-0'):
                        ui.label(val).classes(f'text-xl font-mono font-bold text-{col}-400')
                        ui.label(label).classes('text-xs text-gray-400 font-bold')

                head_stat('SERVERS', data['servers'], 'blue')
                head_stat('NODES', data['nodes'], 'purple')
                head_stat('TRAFFIC', data['traffic'], 'green')

        # 2. 内容区域 (左右布局)
        with ui.row().classes('w-full flex-grow p-6 gap-6 overflow-hidden'):
            # 左侧：流量排行
            with ui.card().classes('w-2/3 h-full bg-slate-800/80 border border-white/5 p-4 rounded-xl'):
                ui.label('流量排行 (Top 10)').classes('font-bold text-gray-300 mb-2')
                ui.echart({
                    'backgroundColor': 'transparent',
                    'tooltip': {'trigger': 'axis'},
                    'grid': {'left': '2%', 'right': '2%', 'bottom': '2%', 'containLabel': True},
                    'xAxis': {
                        'type': 'category', 'data': data['bar_chart']['names'],
                        'axisLabel': {'rotate': 30, 'color': '#94a3b8'},
                        'axisLine': {'lineStyle': {'color': '#334155'}}
                    },
                    'yAxis': {
                        'type': 'value',
                        'splitLine': {'lineStyle': {'color': '#334155'}},
                        'axisLabel': {'color': '#94a3b8'}
                    },
                    'series': [{'type': 'bar', 'data': data['bar_chart']['values'], 'itemStyle': {'color': '#6366f1'},
                                'barWidth': '40%'}]
                }).classes('w-full h-full')

            # 右侧：分布饼图
            with ui.card().classes('w-1/3 h-full bg-slate-800/80 border border-white/5 p-4 rounded-xl'):
                ui.label('区域分布').classes('font-bold text-gray-300 mb-2')
                ui.echart({
                    'backgroundColor': 'transparent',
                    'tooltip': {'trigger': 'item'},
                    'legend': {'bottom': '5%', 'textStyle': {'color': '#cbd5e1'}},
                    'series': [{
                        'type': 'pie', 'radius': ['40%', '65%'], 'center': ['50%', '40%'],
                        'data': data['pie_chart'],
                        'label': {'color': '#fff'}
                    }]
                }).classes('w-full h-full')


# ================= 路由入口 =================
async def status_page_router(request: Request):
    """公开监控页面的统一入口"""
    ua = request.headers.get('user-agent', '').lower()
    is_mobile = any(k in ua for k in ['mobile', 'android', 'iphone'])

    if is_mobile:
        await render_mobile_status_page()
    else:
        await render_desktop_status_page()