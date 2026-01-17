# core/storage.py
import json
import os
import shutil
import uuid
import logging
import asyncio
from nicegui import run

# 引入配置和状态
from core.config import (
    DATA_DIR, CONFIG_FILE, SUBS_FILE,
    NODES_CACHE_FILE, ADMIN_CONFIG_FILE, GLOBAL_SSH_KEY_FILE
)
from core.state import (
    SERVERS_CACHE, SUBS_CACHE, NODES_DATA, ADMIN_CONFIG,
    FILE_LOCK, GLOBAL_UI_VERSION
)

logger = logging.getLogger("Core.Storage")


# ================= 初始化数据 =================
def init_data():
    # 引用 state 中的全局变量
    import core.state as state

    logger.info(f"正在读取数据... (目标: {DATA_DIR})")

    # 1. 加载服务器
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                raw_data = json.load(f)
                # 过滤掉非 dict 数据，防止报错
                state.SERVERS_CACHE.extend([s for s in raw_data if isinstance(s, dict)])
            logger.info(f"✅ 成功加载服务器: {len(state.SERVERS_CACHE)} 台")
        except Exception as e:
            logger.error(f"❌ 读取 servers.json 失败: {e}")
    else:
        logger.warning(f"⚠️ 未找到服务器配置文件: {CONFIG_FILE}")

    # 2. 加载订阅
    if os.path.exists(SUBS_FILE):
        try:
            with open(SUBS_FILE, 'r', encoding='utf-8') as f:
                state.SUBS_CACHE.extend(json.load(f))
        except:
            pass

    # 3. 加载节点缓存
    if os.path.exists(NODES_CACHE_FILE):
        if os.path.isdir(NODES_CACHE_FILE):
            try:
                shutil.rmtree(NODES_CACHE_FILE)
                logger.info("♻️ 已自动删除错误的缓存文件夹")
            except:
                pass
        else:
            try:
                with open(NODES_CACHE_FILE, 'r', encoding='utf-8') as f:
                    state.NODES_DATA.update(json.load(f))
                count = sum([len(v) for v in state.NODES_DATA.values() if isinstance(v, list)])
                logger.info(f"✅ 加载缓存节点: {count} 个")
            except:
                pass

    # 4. 加载管理员配置
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

    # 保存一次配置确保持久化
    try:
        with open(ADMIN_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(state.ADMIN_CONFIG, f, indent=4, ensure_ascii=False)
    except Exception as e:
        logger.error(f"❌ 配置保存失败: {e}")


# ================= 文件操作辅助 =================
def _save_file_sync_internal(filename, data):
    """同步写入文件的内部函数，供 run.io_bound 调用"""
    temp_file = f"{filename}.{uuid.uuid4()}.tmp"
    try:
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        shutil.move(temp_file, filename)
    except Exception as e:
        if os.path.exists(temp_file): os.remove(temp_file)
        raise e


async def safe_save(filename, data):
    """线程安全的文件保存"""
    async with FILE_LOCK:
        try:
            await run.io_bound(_save_file_sync_internal, filename, data)
        except Exception as e:
            logger.error(f"❌ 保存 {filename} 失败: {e}")


# ================= 具体保存函数 =================

async def save_servers():
    """保存服务器列表"""
    import core.state as state
    import time
    await safe_save(CONFIG_FILE, state.SERVERS_CACHE)
    # 更新版本号，通知 UI 可能需要重绘
    state.GLOBAL_UI_VERSION = time.time()


async def save_admin_config():
    """保存管理配置"""
    import core.state as state
    import time
    await safe_save(ADMIN_CONFIG_FILE, state.ADMIN_CONFIG)
    state.GLOBAL_UI_VERSION = time.time()


async def save_subs():
    """保存订阅"""
    import core.state as state
    await safe_save(SUBS_FILE, state.SUBS_CACHE)


async def save_nodes_cache():
    """保存节点缓存"""
    import core.state as state
    try:
        data_snapshot = state.NODES_DATA.copy()
        await safe_save(NODES_CACHE_FILE, data_snapshot)
    except Exception as e:
        logger.error(f"❌ 保存缓存失败: {e}")


# ================= SSH 密钥操作 =================
def load_global_key():
    if os.path.exists(GLOBAL_SSH_KEY_FILE):
        with open(GLOBAL_SSH_KEY_FILE, 'r') as f: return f.read()
    return ""


def save_global_key(content):
    with open(GLOBAL_SSH_KEY_FILE, 'w') as f: f.write(content)