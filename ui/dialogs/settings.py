# ui/dialogs/settings.py
import json
import time
from nicegui import ui
from core.state import ADMIN_CONFIG, SERVERS_CACHE, SUBS_CACHE, NODES_DATA
from core.storage import save_admin_config, save_servers, save_subs, save_nodes_cache, load_global_key, save_global_key
from services.geoip import detect_country_group
from ui.common import safe_notify, safe_copy_to_clipboard


# ================= Cloudflare =================
def open_cloudflare_settings_dialog():
    with ui.dialog() as d, ui.card().classes('w-[500px] p-6 bg-white rounded-xl'):
        ui.label('Cloudflare 配置').classes('text-lg font-bold mb-4')
        token = ui.input('API Token', value=ADMIN_CONFIG.get('cf_api_token', '')).props(
            'type=password outlined dense').classes('w-full mb-2')
        domain = ui.input('根域名 (example.com)', value=ADMIN_CONFIG.get('cf_root_domain', '')).props(
            'outlined dense').classes('w-full mb-4')

        async def save():
            ADMIN_CONFIG['cf_api_token'] = token.value.strip()
            ADMIN_CONFIG['cf_root_domain'] = domain.value.strip()
            await save_admin_config()
            safe_notify('已保存', 'positive');
            d.close()

        ui.button('保存', on_click=save).classes('w-full bg-slate-900 text-white')
    d.open()


# ================= 全局 SSH =================
def open_global_settings_dialog():
    with ui.dialog() as d, ui.card().classes('w-full max-w-2xl p-6 bg-white rounded-xl'):
        ui.label('全局 SSH 私钥').classes('text-xl font-bold mb-2')
        key_area = ui.textarea(value=load_global_key(), placeholder='-----BEGIN OPENSSH PRIVATE KEY-----').classes(
            'w-full font-mono text-xs').props('rows=10 outlined')

        async def save():
            save_global_key(key_area.value)
            safe_notify('密钥已保存', 'positive');
            d.close()

        ui.button('保存', on_click=save).classes('w-full mt-4 bg-slate-900 text-white')
    d.open()


# ================= 备份恢复 =================
async def open_data_mgmt_dialog():
    with ui.dialog() as d, ui.card().classes('w-full max-w-2xl p-0 bg-white rounded-xl overflow-hidden'):
        with ui.tabs().classes('w-full bg-white border-b text-gray-700') as tabs:
            t1 = ui.tab('备份');
            t2 = ui.tab('恢复')
        with ui.tab_panels(tabs, value=t2).classes('w-full p-6'):
            with ui.tab_panel(t1):
                data = {"servers": SERVERS_CACHE, "subs": SUBS_CACHE, "config": ADMIN_CONFIG, "key": load_global_key(),
                        "cache": NODES_DATA}
                js = json.dumps(data, indent=2)
                ui.textarea(value=js).props('readonly outlined').classes('w-full h-48 text-xs font-mono mb-4')
                ui.button('复制 JSON', on_click=lambda: safe_copy_to_clipboard(js)).classes(
                    'w-full bg-slate-800 text-white')
            with ui.tab_panel(t2).classes('flex flex-col gap-4'):
                imp = ui.textarea(placeholder='粘贴 JSON...').classes('w-full h-48 text-xs font-mono').props('outlined')
                chk = ui.checkbox('覆盖同名服务器', value=False).classes('text-gray-600')

                async def run_restore():
                    try:
                        d_in = json.loads(imp.value)
                        existing = {s['url']: i for i, s in enumerate(SERVERS_CACHE)}
                        for s in d_in.get('servers', []):
                            if s['url'] in existing and chk.value:
                                SERVERS_CACHE[existing[s['url']]] = s
                            elif s['url'] not in existing:
                                SERVERS_CACHE.append(s)
                        if 'config' in d_in: ADMIN_CONFIG.update(d_in['config'])
                        if 'key' in d_in: save_global_key(d_in['key'])
                        await save_servers();
                        await save_admin_config()
                        from ui.layout import render_sidebar_content;
                        render_sidebar_content.refresh()
                        safe_notify('恢复成功', 'positive');
                        d.close()
                    except:
                        safe_notify('JSON 格式错误', 'negative')

                ui.button('执行恢复', on_click=run_restore).classes(
                    'w-full bg-blue-500 text-white shadow-md h-10 font-bold text-base')
    d.open()


# ================= 分组管理 (含新建) =================
def open_quick_group_create_dialog(): open_combined_group_management("")


def open_combined_group_management(group_name=""):
    is_new = not group_name
    title = "新建分组 (标签模式)" if is_new else f"管理分组: {group_name}"

    sel_map = {s['url']: (group_name in s.get('tags', []) or s.get('group') == group_name) for s in SERVERS_CACHE}
    row_refs = {}

    with ui.dialog() as d, ui.card().classes(
            'w-[500px] h-[80vh] flex flex-col p-0 bg-white rounded-xl shadow-2xl overflow-hidden'):
        with ui.column().classes('w-full p-4 border-b bg-white gap-3 flex-shrink-0'):
            with ui.row().classes('w-full justify-between items-center'):
                ui.label(title).classes('text-lg font-bold text-gray-800')
                ui.button(icon='close', on_click=d.close).props('flat round dense color=grey')
            name_input = ui.input(label='分组名称', value=group_name, placeholder='例如: 甲骨文云').props(
                'outlined dense bg-color=white').classes('w-full')
            search_input = ui.input(placeholder='🔍 搜索筛选服务器...').props('outlined dense clearable').classes(
                'w-full')

            def on_search(e):
                val = str(e.value).lower().strip()
                for item in row_refs.values(): item['el'].set_visibility(val in item['text'])

            search_input.on_value_change(on_search)

        with ui.row().classes('w-full px-4 py-2 justify-between items-center bg-white border-b'):
            ui.label('勾选加入该组:').classes('text-xs text-gray-500 font-bold')
            ui.link('全选', '#').classes('text-xs text-blue-500').on('click', lambda: [i['chk'].set_value(True) for i in
                                                                                       row_refs.values() if
                                                                                       i['el'].visible])

        with ui.scroll_area().classes('w-full flex-grow p-2 bg-gray-100'):
            with ui.column().classes('w-full gap-1'):
                sorted_srv = sorted(SERVERS_CACHE, key=lambda x: x.get('name', ''))
                for s in sorted_srv:
                    flag = "🏳️"
                    try:
                        flag = detect_country_group(s['name'], s).split(' ')[0]
                    except:
                        pass
                    text = f"{s['name']} {s['url']}".lower()

                    with ui.row().classes(
                            'w-full items-center p-3 bg-white rounded border border-gray-200 cursor-pointer hover:border-blue-400') as row:
                        chk = ui.checkbox(value=sel_map[s['url']]).props('dense')
                        row.on('click', lambda _, c=chk: c.set_value(not c.value));
                        chk.on('click.stop', lambda: None)
                        chk.on_value_change(lambda e, u=s['url']: sel_map.update({u: e.value}))
                        ui.label(flag).classes('text-lg mr-2')
                        ui.label(s['name']).classes('text-sm font-bold text-gray-700 truncate flex-grow')
                    row_refs[s['url']] = {'el': row, 'chk': chk, 'text': text}

        with ui.row().classes('w-full p-4 bg-white border-t justify-end gap-2'):
            if not is_new:
                async def delete():
                    if group_name in ADMIN_CONFIG.get('custom_groups', []): ADMIN_CONFIG['custom_groups'].remove(
                        group_name)
                    for s in SERVERS_CACHE:
                        if group_name in s.get('tags', []): s['tags'].remove(group_name)
                    await save_admin_config();
                    await save_servers()
                    from ui.layout import render_sidebar_content;
                    render_sidebar_content.refresh()
                    safe_notify('已删除', 'positive');
                    d.close()

                ui.button('删除分组', on_click=delete, color='red').props('flat')

            async def save():
                tag = name_input.value.strip()
                if not tag: return
                grps = ADMIN_CONFIG.get('custom_groups', [])
                if not is_new and group_name in grps:
                    grps[grps.index(group_name)] = tag
                elif tag not in grps:
                    grps.append(tag)
                ADMIN_CONFIG['custom_groups'] = grps

                for s in SERVERS_CACHE:
                    should = sel_map.get(s['url'])
                    if 'tags' not in s: s['tags'] = []
                    if should:
                        if tag not in s['tags']: s['tags'].append(tag)
                        if not is_new and group_name in s['tags']: s['tags'].remove(group_name)
                    else:
                        if tag in s['tags']: s['tags'].remove(tag)
                        if not is_new and group_name in s['tags']: s['tags'].remove(group_name)

                await save_admin_config();
                await save_servers()
                from ui.layout import render_sidebar_content;
                render_sidebar_content.refresh()
                safe_notify('保存成功', 'positive');
                d.close()

            ui.button('保存', on_click=save).classes('bg-blue-600 text-white shadow-md')
    d.open()