# ui/dialogs/server_edit.py
import asyncio
from nicegui import ui

from core.state import SERVERS_CACHE, ADMIN_CONFIG
from core.storage import save_servers
from services.ssh_manager import install_probe_on_server
from services.geoip import force_geoip_naming_task
from ui.common import safe_notify


async def open_server_dialog(idx=None):
    is_edit = idx is not None
    data = SERVERS_CACHE[idx].copy() if is_edit else {}

    # 默认值
    if not data.get('ssh_port'): data['ssh_port'] = '22'
    if not data.get('ssh_user'): data['ssh_user'] = 'root'
    if not data.get('ssh_auth_type'): data['ssh_auth_type'] = '全局密钥'

    with ui.dialog() as d, ui.card().classes('w-[450px] p-6 rounded-xl shadow-2xl bg-white'):
        # 1. 标题
        title = '编辑服务器' if is_edit else '添加服务器'
        ui.label(title).classes('text-xl font-bold text-slate-800 mb-4')

        # 2. 顶部 Tab
        with ui.tabs().classes('w-full text-blue-600 bg-transparent') as tabs:
            t_ssh = ui.tab('SSH / 探针', icon='terminal').classes('flex-1')
            t_xui = ui.tab('X-UI面板', icon='settings').classes('flex-1')

        # 3. 通用字段
        with ui.column().classes('w-full mt-4 gap-3'):
            name = ui.input(label='备注名称 (留空自动获取)', value=data.get('name', '')).props(
                'outlined dense').classes('w-full')

            # ✨✨✨ [修复报错]：确保当前分组也在选项列表中 ✨✨✨
            current_group = data.get('group', '默认分组')
            existing_groups = ADMIN_CONFIG.get('custom_groups', []) + ['默认分组']
            # 如果当前分组（例如自动识别的"🇺🇸 美国"）不在列表里，临时加上
            if current_group and current_group not in existing_groups:
                existing_groups.append(current_group)

            grp_opts = sorted(list(set(existing_groups)))

            group = ui.select(grp_opts, label='分组', value=current_group, new_value_mode='add-unique').props(
                'outlined dense').classes('w-full')

        # 4. 面板内容
        with ui.tab_panels(tabs, value=t_ssh).classes('w-full mt-2 animated fadeIn'):

            # SSH 面板
            with ui.tab_panel(t_ssh).classes('p-0 flex flex-col gap-3'):
                ssh_host = ui.input('SSH 主机 IP', value=data.get('ssh_host',
                                                                  data.get('url', '').split('://')[-1].split(':')[
                                                                      0])).props('outlined dense').classes('w-full')

                with ui.row().classes('w-full gap-2'):
                    ssh_user = ui.input('SSH 用户', value=data.get('ssh_user', 'root')).props('outlined dense').classes(
                        'flex-1')
                    ssh_port = ui.input('端口', value=data.get('ssh_port', '22')).props('outlined dense').classes(
                        'w-1/3')

                auth_type = ui.select(['全局密钥', '独立密码', '独立密钥'], label='认证方式',
                                      value=data.get('ssh_auth_type', '全局密钥')).props('outlined dense').classes(
                    'w-full')

                pwd = ui.input('SSH 密码', password=True, value=data.get('ssh_password', '')).props(
                    'outlined dense').classes('w-full')
                pwd.bind_visibility_from(auth_type, 'value', backward=lambda v: v == '独立密码')

                key = ui.textarea('SSH 私钥', value=data.get('ssh_key', '')).props('outlined dense rows=3').classes(
                    'w-full')
                key.bind_visibility_from(auth_type, 'value', backward=lambda v: v == '独立密钥')

            # X-UI 面板
            with ui.tab_panel(t_xui).classes('p-0 flex flex-col gap-3'):
                xui_url = ui.input('面板地址 (http://ip:port)', value=data.get('url', '')).props(
                    'outlined dense').classes('w-full')
                with ui.row().classes('w-full gap-2'):
                    xui_user = ui.input('账号', value=data.get('user', '')).props('outlined dense').classes('flex-1')
                    xui_pass = ui.input('密码', password=True, value=data.get('pass', '')).props(
                        'outlined dense').classes('flex-1')
                xui_prefix = ui.input('API 前缀', value=data.get('prefix', '')).props('outlined dense').classes(
                    'w-full')

                chk_probe = ui.checkbox('启用 Root 探针 (自动安装)', value=data.get('probe_installed', True)).classes(
                    'text-gray-600 font-bold')

        # 5. 底部按钮
        ui.separator().classes('mt-4 mb-4')
        with ui.row().classes('w-full items-center justify-between'):
            with ui.row().classes('items-center gap-1'):
                icon_check = ui.icon('check_box', color='green').classes('text-xl')
                lbl_hint = ui.label('自动使用全局私钥').classes('text-green-600 font-bold text-xs')
                icon_check.bind_visibility_from(auth_type, 'value', backward=lambda v: v == '全局密钥')
                lbl_hint.bind_visibility_from(auth_type, 'value', backward=lambda v: v == '全局密钥')

            async def save():
                new_data = data.copy()
                new_data.update({
                    'name': name.value.strip(),
                    'group': group.value,
                    'ssh_host': ssh_host.value.strip(),
                    'ssh_port': ssh_port.value,
                    'ssh_user': ssh_user.value,
                    'ssh_auth_type': auth_type.value,
                    'ssh_password': pwd.value,
                    'ssh_key': key.value,
                    'url': xui_url.value.strip() if 'xui_url' in locals() else (
                                new_data.get('url') or f"http://{ssh_host.value.strip()}:{ssh_port.value}"),
                    'probe_installed': True
                })

                if 'xui_url' in locals() and xui_url.value:
                    new_data.update({
                        'user': xui_user.value, 'pass': xui_pass.value, 'prefix': xui_prefix.value,
                        'probe_installed': chk_probe.value
                    })

                if is_edit:
                    SERVERS_CACHE[idx] = new_data
                else:
                    SERVERS_CACHE.append(new_data)

                await save_servers()

                if not new_data['name']: asyncio.create_task(force_geoip_naming_task(new_data))
                if new_data.get('probe_installed'): asyncio.create_task(install_probe_on_server(new_data))

                # 刷新 UI
                from ui.layout import render_sidebar_content
                render_sidebar_content.refresh()

                # 尝试刷新当前内容页
                try:
                    from ui.pages.dashboard import refresh_content
                    from core.state import CURRENT_VIEW_STATE
                    await refresh_content(CURRENT_VIEW_STATE['scope'], CURRENT_VIEW_STATE['data'], force_refresh=True)
                except:
                    pass

                safe_notify('保存成功', 'positive')
                d.close()

            # 删除按钮
            if is_edit:
                async def delete():
                    SERVERS_CACHE.pop(idx)
                    await save_servers()
                    from ui.layout import render_sidebar_content
                    render_sidebar_content.refresh()
                    d.close()
                    safe_notify('已删除', 'warning')

                ui.button('删除', on_click=delete, color='red').props('flat dense')

            ui.button('保存配置', icon='save', on_click=save).classes('bg-blue-600 text-white shadow-md px-6')

    d.open()