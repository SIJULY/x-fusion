# services/ssh_manager.py
import paramiko
import io
import asyncio
import logging
from nicegui import run
from core.state import ADMIN_CONFIG
from core.storage import load_global_key, save_servers
from services.install_scripts import PROBE_INSTALL_SCRIPT

logger = logging.getLogger("Services.SSH")


# ================= SSH 连接核心逻辑 =================
def get_ssh_client(server_data):
    """
    建立 SSH 连接 (同步阻塞方法，请在 run.io_bound 中调用)
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    # 1. 解析 IP/域名
    raw_url = server_data.get('url', '')
    if '://' in raw_url:
        host = raw_url.split('://')[-1].split(':')[0]
    else:
        host = raw_url.split(':')[0]

    # 优先使用 ssh_host 字段
    if server_data.get('ssh_host'):
        host = server_data['ssh_host']

    port = int(server_data.get('ssh_port') or 22)
    user = server_data.get('ssh_user') or 'root'
    auth_type = server_data.get('ssh_auth_type', '全局密钥').strip()

    try:
        if auth_type == '独立密码':
            pwd = server_data.get('ssh_password', '')
            if not pwd: raise Exception("密码为空")
            client.connect(host, port, username=user, password=pwd, timeout=5, look_for_keys=False, allow_agent=False)

        elif auth_type == '独立密钥':
            key_content = server_data.get('ssh_key', '')
            if not key_content: raise Exception("独立密钥为空")
            key_file = io.StringIO(key_content)
            try:
                pkey = paramiko.RSAKey.from_private_key(key_file)
            except:
                key_file.seek(0)
                try:
                    pkey = paramiko.Ed25519Key.from_private_key(key_file)
                except:
                    raise Exception("无法识别的私钥格式")
            client.connect(host, port, username=user, pkey=pkey, timeout=5, look_for_keys=False, allow_agent=False)

        else:  # 全局密钥
            g_key = load_global_key()
            if not g_key: raise Exception("全局密钥未配置")
            key_file = io.StringIO(g_key)
            try:
                pkey = paramiko.RSAKey.from_private_key(key_file)
            except:
                key_file.seek(0)
                try:
                    pkey = paramiko.Ed25519Key.from_private_key(key_file)
                except:
                    raise Exception("全局密钥格式错误")
            client.connect(host, port, username=user, pkey=pkey, timeout=5, look_for_keys=False, allow_agent=False)

        return client, f"✅ 已连接 {user}@{host}"

    except Exception as e:
        return None, f"❌ 连接失败: {str(e)}"


def get_ssh_client_sync(server_data):
    """WebSSH 兼容包装器"""
    return get_ssh_client(server_data)


# ================= 远程命令执行 =================
def _ssh_exec_wrapper(server_conf, cmd):
    """
    执行 SSH 命令并返回结果 (同步阻塞)
    返回: (Success: bool, Output: str)
    """
    client, msg = get_ssh_client(server_conf)
    if not client: return False, msg
    try:
        # 设置 120s 超时，防止长时间任务卡死
        stdin, stdout, stderr = client.exec_command(cmd, timeout=120)
        out = stdout.read().decode().strip()
        err = stderr.read().decode().strip()
        client.close()
        return True, out + "\n" + err
    except Exception as e:
        return False, str(e)


# ================= 探针安装/更新 =================
async def install_probe_on_server(server_conf):
    """
    通过 SSH 在目标服务器上安装/更新 Python 探针
    """
    name = server_conf.get('name', 'Unknown')

    # 1. 准备参数
    manager_url = ADMIN_CONFIG.get('manager_base_url', 'http://xui-manager:8080')
    my_token = ADMIN_CONFIG.get('probe_token', 'default_token')
    ping_ct = ADMIN_CONFIG.get('ping_target_ct', '202.102.192.68')
    ping_cu = ADMIN_CONFIG.get('ping_target_cu', '112.122.10.26')
    ping_cm = ADMIN_CONFIG.get('ping_target_cm', '211.138.180.2')

    # 2. 替换脚本变量
    real_script = PROBE_INSTALL_SCRIPT \
        .replace("__MANAGER_URL__", manager_url) \
        .replace("__TOKEN__", my_token) \
        .replace("__SERVER_URL__", server_conf['url']) \
        .replace("__PING_CT__", ping_ct) \
        .replace("__PING_CU__", ping_cu) \
        .replace("__PING_CM__", ping_cm)

    # 3. 执行安装
    def _do_install():
        client = None
        try:
            client, msg = get_ssh_client(server_conf)
            if not client: return False, msg

            # 使用 root 权限执行
            stdin, stdout, stderr = client.exec_command(real_script, timeout=60)
            exit_status = stdout.channel.recv_exit_status()

            if exit_status == 0: return True, "Agent 安装成功"
            return False, f"脚本退出码: {exit_status}"
        except Exception as e:
            return False, str(e)
        finally:
            if client: client.close()

    success, msg = await run.io_bound(_do_install)

    if success:
        server_conf['probe_installed'] = True
        await save_servers()
        logger.info(f"✅ [探针部署] {name} 成功")
    else:
        logger.warning(f"⚠️ [探针部署] {name} 失败: {msg}")

    return success


# ================= 智能 SSH 用户名探测 =================
async def smart_detect_ssh_user_task(server_conf):
    """
    尝试使用 ubuntu/root 等用户名连接，成功后自动安装探针
    """
    candidates = ['ubuntu', 'root']
    ip = server_conf['url'].split('://')[-1].split(':')[0]
    original_user = server_conf.get('ssh_user', '')

    logger.info(f"🕵️‍♂️ [智能探测] 开始探测 {server_conf['name']} ({ip})...")
    found_user = None

    for user in candidates:
        server_conf['ssh_user'] = user
        client, msg = await run.io_bound(get_ssh_client, server_conf)

        if client:
            client.close()
            found_user = user
            logger.info(f"✅ [智能探测] 匹配用户: {user}")
            break

    if found_user:
        server_conf['ssh_user'] = found_user
        server_conf['_ssh_verified'] = True
        await save_servers()

        if ADMIN_CONFIG.get('probe_enabled', False):
            logger.info(f"🚀 [自动部署] 触发探针安装...")
            await asyncio.sleep(2)
            await install_probe_on_server(server_conf)
    else:
        logger.error(f"❌ [智能探测] {server_conf['name']} 连接失败")
        if original_user: server_conf['ssh_user'] = original_user
        await save_servers()