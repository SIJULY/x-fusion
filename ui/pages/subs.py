# ui/pages/subs.py
import uuid
import copy
import asyncio
from nicegui import ui
from core.state import SUBS_CACHE, SERVERS_CACHE, NODES_DATA
from core.storage import save_subs
from ui.common import get_main_content_container, safe_notify, safe_copy_to_clipboard
from services.geoip import detect_country_group


# ================= 高级订阅编辑器 (还原原版复杂逻辑) =================
class AdvancedSubEditor:
    def __init__(self, sub_data=None):
        if sub_data:
            self.sub = copy.deepcopy(sub_data)
        else:
            self.sub = {'name': '', 'token': str(uuid.uuid4()), 'nodes': [], 'options': {}}
        if 'options' not in self.sub: self.sub['options'] = {}

        # 核心数据：有序的选中ID列表
        self.selected_ids = list(self.sub.get('nodes', []))

        self.all_nodes_map = {}
        self.ui_groups = {}
        self.server_expansions = {}
        self.server_items = {}
        self.preview_container = None
        self.list_container = None

    def ui(self, dlg):
        self._preload_data()

        with ui.card().classes(
                'w-full max-w-6xl h-[90vh] flex flex-col p-0 overflow-hidden bg-white rounded-xl shadow-2xl'):
            # --- 顶部 ---
            with ui.row().classes('w-full p-4 border-b bg-gray-50 justify-between items-center flex-shrink-0'):
                with ui.row().classes('items-center gap-2'):
                    ui.icon('tune', color='primary').classes('text-xl')
                    ui.label('订阅高级管理').classes('text-lg font-bold')
                    ui.badge('购物车模式', color='orange').props('outline size=xs')
                ui.button(icon='close', on_click=dlg.close).props('flat round dense color=grey')

            # --- 内容区 (三栏布局) ---
            with ui.row().classes('w-full flex-grow overflow-hidden gap-0 items-stretch'):

                # 1. 左侧：节点仓库 (40%)
                with ui.column().classes('w-2/5 h-full border-r border-gray-200 flex flex-col bg-gray-50'):
                    # 搜索栏
                    with ui.column().classes('w-full p-2 border-b bg-white gap-2 flex-shrink-0'):
                        ui.input(placeholder='🔍 搜索源节点 (如: 日本)', on_change=self.on_search).props(
                            'outlined dense debounce="300"').classes('w-full')
                        with ui.row().classes('w-full justify-between items-center'):
                            ui.label('筛选结果操作:').classes('text-xs text-gray-400')
                            with ui.row().classes('gap-1'):
                                ui.button('全选', icon='add_circle', on_click=lambda: self.batch_select(True)).props(
                                    'unelevated dense size=sm color=blue-6')
                                ui.button('清空', icon='remove_circle',
                                          on_click=lambda: self.batch_select(False)).props(
                                    'flat dense size=sm color=grey-6')

                    # 滚动列表
                    with ui.scroll_area().classes('w-full flex-grow p-2'):
                        self.list_container = ui.column().classes('w-full gap-2')
                        ui.timer(0.1, lambda: asyncio.create_task(self._render_node_tree()), once=True)

                # 2. 中间：功能区 (25%)
                with ui.column().classes(
                        'w-1/4 h-full border-r border-gray-200 flex flex-col bg-white overflow-y-auto p-4 gap-4'):
                    # 基础信息
                    ui.label('① 基础设置').classes('text-xs font-bold text-blue-500 uppercase')
                    ui.input('订阅名称').bind_value(self.sub, 'name').props('outlined dense').classes('w-full')
                    with ui.row().classes('w-full gap-1'):
                        ui.input('Token').bind_value(self.sub, 'token').props('outlined dense').classes('flex-grow')
                        ui.button(icon='refresh',
                                  on_click=lambda: self.sub.update({'token': str(uuid.uuid4())[:8]})).props(
                            'flat dense')

                    ui.separator()

                    # 排序工具
                    ui.label('② 排序工具').classes('text-xs font-bold text-blue-500 uppercase')
                    with ui.grid().classes('w-full grid-cols-2 gap-2'):
                        ui.button('名称 A-Z', on_click=lambda: self.sort_nodes('name_asc')).props(
                            'outline dense size=sm')
                        ui.button('名称 Z-A', on_click=lambda: self.sort_nodes('name_desc')).props(
                            'outline dense size=sm')
                        ui.button('随机打乱', on_click=lambda: self.sort_nodes('random')).props('outline dense size=sm')
                        ui.button('列表倒序', on_click=lambda: self.sort_nodes('reverse')).props(
                            'outline dense size=sm')

                    ui.separator()

                    # 批量重命名
                    ui.label('③ 批量重命名 (正则)').classes('text-xs font-bold text-blue-500 uppercase')
                    with ui.column().classes('w-full gap-2 bg-blue-50 p-2 rounded border border-blue-100'):
                        opt = self.sub.get('options', {})
                        pat = ui.input('正则 (如: ^)', value=opt.get('rename_pattern', '')).props(
                            'outlined dense bg-white dense').classes('w-full')
                        rep = ui.input('替换 (如: VIP-)', value=opt.get('rename_replacement', '')).props(
                            'outlined dense bg-white dense').classes('w-full')

                        def apply_regex():
                            self.sub['options']['rename_pattern'] = pat.value
                            self.sub['options']['rename_replacement'] = rep.value
                            self.update_preview()
                            safe_notify('预览已刷新', 'positive')

                        ui.button('刷新预览', on_click=apply_regex).props(
                            'unelevated dense size=sm color=blue').classes('w-full')

                # 3. 右侧：已选清单 (35%)
                with ui.column().classes('w-[35%] h-full bg-slate-50 flex flex-col'):
                    with ui.row().classes(
                            'w-full p-3 border-b bg-white items-center justify-between shadow-sm z-10 flex-shrink-0'):
                        ui.label('已选节点清单').classes('font-bold text-gray-800')
                        with ui.row().classes('items-center gap-2'):
                            ui.label('').bind_text_from(self, 'selected_ids', lambda x: f"{len(x)}")
                            ui.button('清空全部', icon='delete_forever', on_click=self.clear_all_selected).props(
                                'flat dense size=sm color=red')

                    with ui.scroll_area().classes('w-full flex-grow p-2'):
                        self.preview_container = ui.column().classes('w-full gap-1')
                        self.update_preview()

            # --- 底部保存 ---
            with ui.row().classes('w-full p-3 border-t bg-gray-100 justify-end gap-3 flex-shrink-0'):
                async def save_all():
                    if not self.sub.get('name'): return safe_notify('名称不能为空', 'negative')
                    self.sub['nodes'] = self.selected_ids

                    found = False
                    for i, s in enumerate(SUBS_CACHE):
                        if s.get('token') == self.sub['token']:
                            SUBS_CACHE[i] = self.sub;
                            found = True;
                            break
                    if not found: SUBS_CACHE.append(self.sub)

                    await save_subs()
                    await load_subs_view()  # 刷新父页面
                    dlg.close()
                    safe_notify('✅ 订阅保存成功', 'positive')

                ui.button('保存配置', icon='save', on_click=save_all).classes('bg-slate-800 text-white shadow-lg')

    def _preload_data(self):
        self.all_nodes_map = {}
        for srv in SERVERS_CACHE:
            nodes = (NODES_DATA.get(srv['url'], []) or []) + srv.get('custom_nodes', [])
            for n in nodes:
                key = f"{srv['url']}|{n['id']}"
                self.all_nodes_map[key] = n

    async def _render_node_tree(self):
        self.list_container.clear()
        self.ui_groups = {}
        self.server_expansions = {}
        self.server_items = {}

        # 按分组整理
        grouped = {}
        for srv in SERVERS_CACHE:
            nodes = (NODES_DATA.get(srv['url'], []) or []) + srv.get('custom_nodes', [])
            if not nodes: continue

            g_name = detect_country_group(srv.get('name', ''), srv)
            if g_name in ['默认分组', '自动注册', '未分组'] or not g_name: g_name = '🏳️ 其他地区'

            if g_name not in grouped: grouped[g_name] = []
            grouped[g_name].append({'server': srv, 'nodes': nodes})

        sorted_groups = sorted(grouped.keys())

        with self.list_container:
            for g_name in sorted_groups:
                # 分组折叠面板
                exp = ui.expansion(g_name, icon='folder', value=True).classes(
                    'w-full border rounded bg-white shadow-sm mb-1').props(
                    'header-class="bg-gray-100 text-sm font-bold p-2 min-h-0"')
                self.server_expansions[g_name] = exp
                self.server_items[g_name] = []

                with exp:
                    with ui.column().classes('w-full p-2 gap-2'):
                        for item in grouped[g_name]:
                            srv = item['server']
                            search_key = f"{srv['name']}".lower()

                            # 服务器块容器
                            with ui.column().classes('w-full gap-1'):
                                header = ui.row().classes('w-full items-center gap-1 mt-1 px-1')
                                with header:
                                    ui.icon('dns', size='xs').classes('text-blue-400')
                                    ui.label(srv['name']).classes('text-xs font-bold text-gray-500 truncate')

                                for n in item['nodes']:
                                    key = f"{srv['url']}|{n['id']}"
                                    is_checked = key in self.selected_ids
                                    self.server_items[g_name].append(key)

                                    # 节点行
                                    with ui.row().classes(
                                            'w-full items-center pl-2 py-1 hover:bg-blue-50 rounded cursor-pointer transition border border-transparent') as row:
                                        chk = ui.checkbox(value=is_checked).props('dense size=xs')
                                        chk.disable()
                                        row.on('click', lambda _, k=key: self.toggle_node_from_left(k))
                                        ui.label(n.get('remark', '未命名')).classes(
                                            'text-xs text-gray-700 truncate flex-grow')

                                        # 搜索上下文
                                        full_text = f"{search_key} {n.get('remark', '')} {n.get('protocol', '')}".lower()
                                        self.ui_groups[key] = {'row': row, 'chk': chk, 'text': full_text,
                                                               'group': g_name, 'header': header}

    def toggle_node_from_left(self, key):
        if key in self.selected_ids:
            self.remove_node(key)
        else:
            self.selected_ids.append(key)
            self.update_preview()
            if key in self.ui_groups:
                self.ui_groups[key]['chk'].value = True
                self.ui_groups[key]['row'].classes(add='bg-blue-50 border-blue-200', remove='border-transparent')

    def remove_node(self, key):
        if key in self.selected_ids:
            self.selected_ids.remove(key)
            self.update_preview()
            if key in self.ui_groups:
                self.ui_groups[key]['chk'].value = False
                self.ui_groups[key]['row'].classes(remove='bg-blue-50 border-blue-200', add='border-transparent')

    def clear_all_selected(self):
        for key in list(self.selected_ids): self.remove_node(key)

    def update_preview(self):
        self.preview_container.clear()
        pat = self.sub.get('options', {}).get('rename_pattern', '')
        rep = self.sub.get('options', {}).get('rename_replacement', '')

        with self.preview_container:
            if not self.selected_ids:
                ui.label('请从左侧选择节点').classes('text-gray-300 w-full text-center mt-10')
                return

            for idx, key in enumerate(self.selected_ids):
                node = self.all_nodes_map.get(key)
                if not node: continue

                orig_name = node.get('remark', 'Unknown')
                final_name = orig_name
                if pat:
                    try:
                        import re
                        final_name = re.sub(pat, rep, orig_name)
                    except:
                        pass

                with ui.row().classes(
                        'w-full items-center p-1.5 bg-white border border-gray-200 rounded shadow-sm group hover:border-red-300 transition'):
                    ui.label(str(idx + 1)).classes('text-[10px] text-gray-400 w-5 text-center')

                    with ui.column().classes('gap-0 leading-none flex-grow ml-1'):
                        if final_name != orig_name:
                            ui.label(final_name).classes('text-xs font-bold text-blue-600')
                            ui.label(orig_name).classes('text-[9px] text-gray-400 line-through')
                        else:
                            ui.label(final_name).classes('text-xs font-bold text-gray-700')

                    ui.button(icon='close', on_click=lambda _, k=key: self.remove_node(k)).props(
                        'flat dense size=xs color=red').classes('opacity-0 group-hover:opacity-100')

    def on_search(self, e):
        txt = str(e.value).lower().strip()
        visible_groups = set()

        for key, item in self.ui_groups.items():
            visible = (not txt) or (txt in item['text'])
            item['row'].set_visibility(visible)
            if visible: visible_groups.add(item['group'])

        # 联动折叠面板
        for g_name, exp in self.server_expansions.items():
            is_vis = g_name in visible_groups
            exp.set_visibility(is_vis)
            if txt and is_vis: exp.value = True

    def sort_nodes(self, mode):
        if not self.selected_ids: return safe_notify('列表为空', 'warning')
        objs = [{'key': k, 'name': self.all_nodes_map.get(k, {}).get('remark', '').lower()} for k in self.selected_ids]

        if mode == 'name_asc':
            objs.sort(key=lambda x: x['name'])
        elif mode == 'name_desc':
            objs.sort(key=lambda x: x['name'], reverse=True)
        elif mode == 'random':
            import random; random.shuffle(objs)
        elif mode == 'reverse':
            objs.reverse()

        self.selected_ids = [x['key'] for x in objs]
        self.update_preview()
        safe_notify(f'已按 {mode} 重新排序', 'positive')

    def batch_select(self, val):
        count = 0
        for key, item in self.ui_groups.items():
            if item['row'].visible:  # 只操作搜索可见的
                if val and key not in self.selected_ids:
                    self.selected_ids.append(key)
                    item['chk'].value = True;
                    item['row'].classes(add='bg-blue-50 border-blue-200', remove='border-transparent')
                    count += 1
                elif not val and key in self.selected_ids:
                    self.selected_ids.remove(key)
                    item['chk'].value = False;
                    item['row'].classes(remove='bg-blue-50 border-blue-200', add='border-transparent')
                    count += 1
        if count > 0:
            self.update_preview()
            safe_notify(f"已{'添加' if val else '移除'} {count} 个节点", "positive")


def open_sub_editor(sub_data):
    with ui.dialog() as d: AdvancedSubEditor(sub_data).ui(d); d.open()


# ================= 订阅管理主视图 (还原 V30 样式) =================
async def load_subs_view():
    container = get_main_content_container()
    container.clear()

    # 统计有效节点
    all_active_keys = set()
    for srv in SERVERS_CACHE:
        nodes = (NODES_DATA.get(srv['url'], []) or []) + srv.get('custom_nodes', [])
        for n in nodes: all_active_keys.add(f"{srv['url']}|{n['id']}")

    with container:
        ui.label('订阅管理').classes('text-2xl font-bold mb-4 text-slate-800')
        with ui.row().classes('w-full mb-4 justify-end'):
            ui.button('新建订阅', icon='add', color='green', on_click=lambda: open_sub_editor(None)).props(
                'unelevated shadow-md')

        if not SUBS_CACHE:
            with ui.column().classes('w-full h-64 justify-center items-center text-gray-400'):
                ui.icon('rss_feed', size='4rem');
                ui.label('暂无订阅')

        for idx, sub in enumerate(SUBS_CACHE):
            with ui.card().classes(
                    'w-full p-4 mb-3 shadow-sm hover:shadow-md transition border-l-4 border-blue-500 rounded-lg bg-white'):
                # 顶部信息栏
                with ui.row().classes('justify-between w-full items-start'):
                    with ui.column().classes('gap-1'):
                        with ui.row().classes('items-center gap-2'):
                            ui.label(sub.get('name', '未命名订阅')).classes('font-bold text-lg text-slate-800')
                            ui.badge('普通', color='blue').props('outline size=xs')

                        saved_ids = set(sub.get('nodes', []))
                        valid_count = len(saved_ids.intersection(all_active_keys))
                        ui.label(f"⚡ 包含节点: {valid_count} (有效) / {len(saved_ids)} (总计)").classes(
                            'text-xs font-bold text-green-600 font-mono')

                    with ui.row().classes('gap-2'):
                        ui.button('管理订阅', icon='tune', on_click=lambda _, s=sub: open_sub_editor(s)).props(
                            'unelevated dense size=sm color=blue-7')

                        async def dl(i=idx):
                            SUBS_CACHE.pop(i);
                            await save_subs();
                            await load_subs_view();
                            safe_notify('已删除')

                        ui.button(icon='delete', color='red', on_click=dl).props('flat dense size=sm')

                ui.separator().classes('my-3 opacity-50')

                # 链接显示区
                try:
                    origin = await ui.run_javascript('return window.location.origin', timeout=3.0)
                except:
                    origin = ""
                path = f"/sub/{sub['token']}"
                raw_url = f"{origin}{path}"

                with ui.row().classes(
                        'w-full items-center gap-2 bg-slate-100 p-2 rounded justify-between border border-slate-200'):
                    with ui.row().classes('items-center gap-2 flex-grow overflow-hidden'):
                        ui.icon('link').classes('text-gray-400 text-sm')
                        ui.label(raw_url).classes('text-xs font-mono text-slate-600 truncate select-all')

                    with ui.row().classes('gap-1'):
                        def btn_copy(icon, color, text, func):
                            ui.button(icon=icon, on_click=func).props(
                                f'flat dense round size=xs text-color={color}').tooltip(text)

                        btn_copy('content_copy', 'grey-7', '复制原始链接', lambda u=raw_url: safe_copy_to_clipboard(u))
                        btn_copy('bolt', 'orange', '复制 Surge',
                                 lambda u=f"{origin}/get/sub/surge/{sub['token']}": safe_copy_to_clipboard(u))
                        btn_copy('cloud_queue', 'green', '复制 Clash',
                                 lambda u=f"{origin}/get/sub/clash/{sub['token']}": safe_copy_to_clipboard(u))