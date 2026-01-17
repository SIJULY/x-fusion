import requests
import asyncio
from concurrent.futures import ThreadPoolExecutor
from app.core.state import BG_EXECUTOR, SYNC_SEMAPHORE, NODES_DATA
from app.core.data_manager import save_servers
from app.utils.geo_ip import auto_prepend_flag

managers = {}


class XUIManager:
    def __init__(self, url, username, password, api_prefix=None):
        self.original_url = str(url).strip().rstrip('/')
        self.url = self.original_url
        self.username = str(username).strip()
        self.password = str(password).strip()
        self.api_prefix = f"/{api_prefix.strip('/')}" if api_prefix else None
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'Mozilla/5.0', 'Connection': 'close'})
        self.session.verify = False
        self.login_path = None

    def _request(self, method, path, **kwargs):
        target_url = f"{self.url}{path}"
        for attempt in range(2):
            try:
                if method == 'POST':
                    return self.session.post(target_url, timeout=5, allow_redirects=False, **kwargs)
                else:
                    return self.session.get(target_url, timeout=5, allow_redirects=False, **kwargs)
            except:
                if attempt == 1: return None

    def login(self):
        if self.login_path:
            if self._try_login_at(self.login_path): return True
            self.login_path = None
        paths = ['/login', '/xui/login', '/panel/login']
        if self.api_prefix: paths.insert(0, f"{self.api_prefix}/login")
        protocols = [self.original_url]
        if '://' not in self.original_url:
            protocols = [f"http://{self.original_url}", f"https://{self.original_url}"]
        elif self.original_url.startswith('http://'):
            protocols.append(self.original_url.replace('http://', 'https://'))
        elif self.original_url.startswith('https://'):
            protocols.append(self.original_url.replace('https://', 'http://'))
        for proto_url in protocols:
            self.url = proto_url
            for path in paths:
                if self._try_login_at(path):
                    self.login_path = path
                    return True
        return False

    def _try_login_at(self, path):
        try:
            r = self._request('POST', path, data={'username': self.username, 'password': self.password})
            if r and r.status_code == 200 and r.json().get('success') == True: return True
            return False
        except:
            return False

    def get_inbounds(self):
        if not self.login(): return None
        candidates = []
        if self.login_path: candidates.append(self.login_path.replace('login', 'inbound/list'))
        defaults = ['/xui/inbound/list', '/panel/inbound/list', '/inbound/list']
        if self.api_prefix: defaults.insert(0, f"{self.api_prefix}/inbound/list")
        for d in defaults:
            if d not in candidates: candidates.append(d)
        for path in candidates:
            r = self._request('POST', path)
            if r and r.status_code == 200:
                try:
                    res = r.json()
                    if res.get('success'): return res.get('obj')
                except:
                    pass
        return None


def get_manager(server_conf):
    key = server_conf['url']
    if key not in managers or managers[key].username != server_conf['user']:
        managers[key] = XUIManager(server_conf['url'], server_conf['user'], server_conf['pass'],
                                   server_conf.get('prefix'))
    return managers[key]


# ================= 业务逻辑函数 =================

async def run_in_bg_executor(func, *args):
    loop = asyncio.get_running_loop()
    # 如果 BG_EXECUTOR 未初始化，可能需要在这里处理，或者假设 state 中已正确初始化
    return await loop.run_in_executor(BG_EXECUTOR, func, *args)


async def fetch_inbounds_safe(server_conf, force_refresh=False, sync_name=False):
    url = server_conf['url']

    # 缓存命中
    if not force_refresh and url in NODES_DATA: return NODES_DATA[url]

    async with SYNC_SEMAPHORE:
        try:
            mgr = get_manager(server_conf)
            inbounds = await run_in_bg_executor(mgr.get_inbounds)
            if inbounds is None:
                # 重试一次
                mgr = managers[server_conf['url']] = XUIManager(server_conf['url'], server_conf['user'],
                                                                server_conf['pass'], server_conf.get('prefix'))
                inbounds = await run_in_bg_executor(mgr.get_inbounds)

            if inbounds is not None:
                NODES_DATA[url] = inbounds
                server_conf['_status'] = 'online'

                # 名称同步逻辑
                if sync_name:
                    try:
                        if len(inbounds) > 0:
                            remote_name = inbounds[0].get('remark', '').strip()
                            if remote_name:
                                current_full_name = server_conf.get('name', '')
                                if ' ' in current_full_name:
                                    parts = current_full_name.split(' ', 1)
                                    current_flag, current_text = parts[0], parts[1].strip()
                                else:
                                    current_flag, current_text = "", current_full_name

                                if current_text != remote_name:
                                    if current_flag:
                                        new_name = f"{current_flag} {remote_name}"
                                    else:
                                        new_name = await auto_prepend_flag(remote_name, url)
                                    server_conf['name'] = new_name
                                    asyncio.create_task(save_servers())
                    except:
                        pass

                return inbounds

            NODES_DATA[url] = []
            server_conf['_status'] = 'offline'
            return []

        except Exception:
            NODES_DATA[url] = []
            server_conf['_status'] = 'error'
            return []