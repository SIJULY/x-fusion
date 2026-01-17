# ui/pages/router.py
import logging
from nicegui import ui
from core.state import CURRENT_VIEW_STATE, REFRESH_LOCKS
from ui.common import get_main_content_container, safe_notify

logger = logging.getLogger("Router")


async def route_to(scope, data=None, force_refresh=False):
    """
    统一路由函数
    scope: 'DASHBOARD', 'ALL', 'TAG', 'COUNTRY', 'SINGLE', 'SUBS', 'PROBE'
    """
    container = get_main_content_container()
    if not container:
        logger.error("Content container not found!")
        return

    # 更新全局状态
    CURRENT_VIEW_STATE['scope'] = scope
    CURRENT_VIEW_STATE['data'] = data

    # 清理容器
    container.clear()

    try:
        # 使用局部导入避免循环引用
        if scope == 'DASHBOARD':
            from ui.pages.dashboard import load_dashboard_stats
            await load_dashboard_stats()

        elif scope in ['ALL', 'TAG', 'COUNTRY', 'SINGLE']:
            # 这些视图共用 dashboard.py 中的刷新逻辑
            from ui.pages.dashboard import refresh_content
            # 重置分页为 1
            CURRENT_VIEW_STATE['page'] = 1
            await refresh_content(scope, data, force_refresh=force_refresh)

        elif scope == 'SUBS':
            from ui.pages.subs import load_subs_view
            await load_subs_view()

        elif scope == 'PROBE':
            from ui.pages.probe import render_probe_page
            await render_probe_page()

        else:
            with container:
                ui.label(f"Unknown Route: {scope}")

    except Exception as e:
        logger.error(f"Routing Error ({scope}): {e}")
        safe_notify(f"页面加载失败: {e}", "negative")
        import traceback
        traceback.print_exc()