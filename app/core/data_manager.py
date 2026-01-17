import json
import os
import shutil
import uuid
import logging
import time
from nicegui import run

from app.core.config import (
    CONFIG_FILE, SUBS_FILE, NODES_CACHE_FILE,
    ADMIN_CONFIG_FILE, GLOBAL_SSH_KEY_FILE
)
from app.core.state import (
    SERVERS_CACHE, SUBS_CACHE, NODES_DATA, ADMIN_CONFIG,
    FILE_LOCK, GLOBAL_UI_VERSION
)

logger = logging.getLogger("DataManager")


# ================= 同步读写底层函数 =================

def _save_file_sync_internal(filename, data):
    """原子写入文件 (写临时文件 -> 移动)"""
    temp_file = f"{filename}.{uuid.uuid4()}.tmp"
    try:
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        shutil.move(temp_file, filename)
    except Exception as e:
        if os.path.exists(temp_file): os.remove(temp_file)
        raise e


def load_global_key():
    if os.path.exists(GLOBAL_SSH_KEY_FILE):
        with open(GLOBAL_SSH_KEY_FILE, 'r') as f: return f.read()
    return ""


def save_global_key(content):
    with open(GLOBAL_SSH_KEY_FILE, 'w') as f: f.write(content)


# ================= 异步封装 =================

async def safe_save(filename, data):
    async with FILE_LOCK:
        try:
            await run.io_bound(_save_file_sync_internal, filename, data)
        except Exception as e:
            logger.error(f"❌ 保存 {filename} 失败: {e}")


async def save_servers():
    # 引用 state 中的变量
    import app.core.state as state
    await safe_save(CONFIG_FILE, state.SERVERS_CACHE)
    # 更新版本号，通知 UI 重绘
    state.GLOBAL_UI_VERSION = time.time()


async def save_admin_config():
    import app.core.state as state
    await safe_save(ADMIN_CONFIG_FILE, state.ADMIN_CONFIG)
    state.GLOBAL_UI_VERSION = time.time()


async def save_subs():
    import app.core.state as state
    await safe_save(SUBS_FILE, state.SUBS_CACHE)


async def save_nodes_cache():
    import app.core.state as state
    try:
        data_snapshot = state.NODES_DATA.copy()
        await safe_save(NODES_CACHE_FILE, data_snapshot)
    except Exception as e:
        logger.error(f"❌ 保存缓存失败: {e}")


# ================= 初始化加载 =================

async def init_data_async():
    """系统启动时调用，加载所有 JSON"""
    import app.core.state as state

    logger.info(f"正在读取数据... (目标: {CONFIG_FILE.parent})")

    # 1. 加载服务器
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                raw_data = json.load(f)
                state.SERVERS_CACHE.clear()
                state.SERVERS_CACHE.extend([s for s in raw_data if isinstance(s, dict)])
            logger.info(f"✅ 成功加载服务器: {len(state.SERVERS_CACHE)} 台")
        except Exception as e:
            logger.error(f"❌ 读取 servers.json 失败: {e}")
            state.SERVERS_CACHE.clear()

    # 2. 加载订阅
    if os.path.exists(SUBS_FILE):
        try:
            with open(SUBS_FILE, 'r', encoding='utf-8') as f:
                state.SUBS_CACHE.clear()
                state.SUBS_CACHE.extend(json.load(f))
        except:
            state.SUBS_CACHE.clear()

    # 3. 加载缓存
    if os.path.exists(NODES_CACHE_FILE):
        try:
            with open(NODES_CACHE_FILE, 'r', encoding='utf-8') as f:
                state.NODES_DATA.update(json.load(f))
            count = sum([len(v) for v in state.NODES_DATA.values() if isinstance(v, list)])
            logger.info(f"✅ 加载缓存节点: {count} 个")
        except:
            state.NODES_DATA.clear()

    # 4. 加载配置
    if os.path.exists(ADMIN_CONFIG_FILE):
        try:
            with open(ADMIN_CONFIG_FILE, 'r', encoding='utf-8') as f:
                state.ADMIN_CONFIG.update(json.load(f))
        except:
            pass

    # 初始化默认设置
    if 'probe_enabled' not in state.ADMIN_CONFIG:
        state.ADMIN_CONFIG['probe_enabled'] = True
    if 'probe_token' not in state.ADMIN_CONFIG:
        state.ADMIN_CONFIG['probe_token'] = uuid.uuid4().hex

    # 保存一次确保持久化
    await save_admin_config()