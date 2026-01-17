# state.py
import asyncio
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor

# 全局变量初始化
SERVERS_CACHE = []
SUBS_CACHE = []
NODES_DATA = {}
ADMIN_CONFIG = {}
IP_GEO_CACHE = {}
DNS_CACHE = {}
DNS_WAITING_LABELS = {}
PROBE_DATA_CACHE = {}
PING_TREND_CACHE = {}
PING_CACHE = {}
RENDERED_CARDS = {}
LAST_SYNC_MAP = {}
REFRESH_LOCKS = set()
EXPANDED_GROUPS = set()

# UI 引用容器
DASHBOARD_REFS = {
    'servers': None, 'nodes': None, 'traffic': None, 'subs': None,
    'bar_chart': None, 'pie_chart': None, 'stat_up': None, 'stat_down': None, 'stat_avg': None,
    'map': None, 'map_info': None
}
SIDEBAR_UI_REFS = {'groups': {}, 'rows': {}}
CURRENT_VIEW_STATE = {'scope': 'DASHBOARD', 'data': None, 'page': 1}

# 线程/进程池
BG_EXECUTOR = ThreadPoolExecutor(max_workers=20)
PROCESS_POOL = None # 在 main.py 启动时初始化
SYNC_SEMAPHORE = asyncio.Semaphore(50)
FILE_LOCK = asyncio.Lock()

# 全局版本控制
import time
GLOBAL_UI_VERSION = time.time()

# 用于跨模块调用的 UI 刷新钩子 (在 ui_layout.py 中赋值)
refresh_dashboard_ui_func = None
render_sidebar_content_func = None
refresh_content_func = None