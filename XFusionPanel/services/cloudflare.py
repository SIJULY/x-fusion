# services/cloudflare.py
import requests
from nicegui import run
from core.state import ADMIN_CONFIG


class CloudflareHandler:
    def __init__(self):
        self.token = ADMIN_CONFIG.get('cf_api_token', '')
        self.email = ADMIN_CONFIG.get('cf_email', '')
        self.root_domain = ADMIN_CONFIG.get('cf_root_domain', '')
        self.base_url = "https://api.cloudflare.com/client/v4"

    def _headers(self):
        h = {"Content-Type": "application/json"}
        if self.email and "global" in self.token.lower():
            h["X-Auth-Email"] = self.email
            h["X-Auth-Key"] = self.token
        else:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def get_zone_id(self, domain_name=None):
        target = self.root_domain
        if domain_name and domain_name.endswith(self.root_domain):
            target = self.root_domain

        url = f"{self.base_url}/zones?name={target}"
        try:
            r = requests.get(url, headers=self._headers(), timeout=10)
            data = r.json()
            if data.get('success') and len(data['result']) > 0:
                return data['result'][0]['id'], None
            return None, f"未找到 Zone: {target}"
        except Exception as e:
            return None, str(e)

    def set_ssl_flexible(self, zone_id):
        url = f"{self.base_url}/zones/{zone_id}/settings/ssl"
        try:
            requests.patch(url, headers=self._headers(), json={"value": "flexible"}, timeout=10)
        except:
            pass

    async def auto_configure(self, ip, sub_prefix):
        """配置 A 记录并开启 CDN"""
        if not self.token: return False, "未配置 API Token"

        def _task():
            zone_id, err = self.get_zone_id()
            if not zone_id: return False, err

            self.set_ssl_flexible(zone_id)

            full_domain = f"{sub_prefix}.{self.root_domain}"
            url = f"{self.base_url}/zones/{zone_id}/dns_records"
            payload = {"type": "A", "name": full_domain, "content": ip, "ttl": 1, "proxied": True}

            try:
                r = requests.post(url, headers=self._headers(), json=payload, timeout=10)
                if r.json().get('success'): return True, f"解析成功: {full_domain}"
                return False, f"API报错: {r.text}"
            except Exception as e:
                return False, str(e)

        return await run.io_bound(_task)

    async def delete_record_by_domain(self, domain_to_delete):
        """删除 DNS 记录"""
        if not self.token or self.root_domain not in domain_to_delete:
            return False, "安全拦截或配置缺失"

        def _task():
            zone_id, err = self.get_zone_id(domain_to_delete)
            if not zone_id: return False, err

            search_url = f"{self.base_url}/zones/{zone_id}/dns_records?name={domain_to_delete}"
            try:
                r = requests.get(search_url, headers=self._headers(), timeout=10)
                records = r.json().get('result', [])

                deleted_count = 0
                for rec in records:
                    requests.delete(f"{self.base_url}/zones/{zone_id}/dns_records/{rec['id']}", headers=self._headers())
                    deleted_count += 1
                return True, f"已清理 {deleted_count} 条记录"
            except Exception as e:
                return False, str(e)

        return await run.io_bound(_task)