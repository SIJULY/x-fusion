import asyncio
import time
from concurrent.futures import ThreadPoolExecutor

# ================= 核心数据缓存 =================
# 服务器列表 (从 servers.json 加载)
SERVERS_CACHE = []

# 订阅列表 (从 subscriptions.json 加载)
SUBS_CACHE = []

# 节点数据缓存 (面板API获取的实时节点信息)
# 格式: {'url': [node1, node2, ...]}
NODES_DATA = {}

# 全局管理配置 (从 admin_config.json 加载)
ADMIN_CONFIG = {}

# ================= 探针与监控状态 =================
# 探针实时数据 (Agent 上报的 CPU/内存/Ping 等)
PROBE_DATA_CACHE = {}

# 历史延迟/流量趋势数据 (用于绘图)
# 格式: {'url': [{'ts': 123, 'ct': 50, 'cm': 60, ...}, ...]}
PING_TREND_CACHE = {}

# IP地理位置缓存 (避免重复请求 API)
IP_GEO_CACHE = {}

# DNS解析缓存
DNS_CACHE = {}

# ================= 并发与任务控制 =================
# 全局文件写入锁 (防止多协程同时写文件)
FILE_LOCK = asyncio.Lock()

# 进程池执行器 (在 main.py/scheduler.py 中初始化)
# 用于执行高 CPU 消耗任务 (如 Ping, SSH连接)
PROCESS_POOL = None

# ✨✨✨ [修复补全] 后台专用线程池 ✨✨✨
# 用于处理 HTTP 请求等 IO 密集型后台任务
BG_EXECUTOR = ThreadPoolExecutor(max_workers=20)

# 后台同步任务的信号量 (控制并发数)
# 允许同时进行 50 个 HTTP/SSH 请求
SYNC_SEMAPHORE = asyncio.Semaphore(50)

# ================= UI 状态与引用 =================
# 存储仪表盘 UI 元素的引用 (让后台任务能刷新前台)
DASHBOARD_REFS = {
    'servers': None, 'nodes': None, 'traffic': None, 'subs': None,
    'bar_chart': None, 'pie_chart': None, 'stat_up': None, 'stat_down': None,
    'map': None, 'map_info': None
}

# 全局 UI 版本号 (用于通知 Status 页面重绘结构)
GLOBAL_UI_VERSION = time.time()

# 侧边栏 UI 引用 (用于局部刷新)
SIDEBAR_UI_REFS = {
    'groups': {},
    'rows': {}
}

# 展开的分组集合
EXPANDED_GROUPS = set()

# 视图状态 (记录当前用户正在查看什么)
CURRENT_VIEW_STATE = {'scope': 'DASHBOARD', 'data': None}

# ================= 其他 =================
# 仪表盘刷新锁
REFRESH_LOCKS = set()
LAST_SYNC_MAP = {}