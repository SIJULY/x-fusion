# core/state.py
import asyncio
import time
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor

# ================= 数据缓存 (内存中) =================
SERVERS_CACHE = []      # 服务器列表
SUBS_CACHE = []         # 订阅列表
NODES_DATA = {}         # 节点详情缓存 {url: [nodes]}
ADMIN_CONFIG = {}       # 管理员配置
IP_GEO_CACHE = {}       # IP 地理位置缓存
DNS_CACHE = {}          # DNS 解析缓存
PROBE_DATA_CACHE = {}   # 探针推送的数据缓存
PING_TREND_CACHE = {}   # Ping 历史趋势缓存
PING_CACHE = {}         # ✨ [补全] 实时 Ping 结果缓存

# ================= 运行时状态 =================
GLOBAL_UI_VERSION = time.time()  # 用于通知前端刷新的版本号
FILE_LOCK = asyncio.Lock()       # 文件写入锁
EXPANDED_GROUPS = set()          # UI 侧边栏展开状态

# ================= 线程/进程池 =================
# 将在 main.py 启动时初始化
PROCESS_POOL: ProcessPoolExecutor = None
BG_EXECUTOR = ThreadPoolExecutor(max_workers=20)
SYNC_SEMAPHORE = asyncio.Semaphore(50)

# ================= UI 引用 (用于后台任务更新前端) =================
# 存储仪表盘 UI 元素的引用
DASHBOARD_REFS = {
    'servers': None, 'nodes': None, 'traffic': None, 'subs': None,
    'bar_chart': None, 'pie_chart': None, 'stat_up': None,
    'stat_down': None, 'stat_avg': None,
    'map': None, 'map_info': None
}

# 存储 DNS 等待标签的引用
DNS_WAITING_LABELS = {}

# 存储侧边栏 UI 引用
SIDEBAR_UI_REFS = {
    'groups': {},
    'rows': {}
}

# 存储当前视图状态
CURRENT_VIEW_STATE = {'scope': 'DASHBOARD', 'data': None, 'page': 1}

# 存储刷新锁和上次同步时间
REFRESH_LOCKS = set()
LAST_SYNC_MAP = {}