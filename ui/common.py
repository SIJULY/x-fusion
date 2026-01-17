# ui/common.py
import json
from nicegui import ui, app

# ================= 全局内容容器 =================
# 这个容器在 layout.py 中初始化，在 pages 切换时被清空和重绘
content_container = None

def set_main_content_container(container):
    global content_container
    content_container = container

def get_main_content_container():
    return content_container

# ================= 通用工具函数 =================

def safe_notify(message, type='info', timeout=3000):
    try: ui.notify(message, type=type, timeout=timeout)
    except: pass

def format_bytes(size):
    if not size: return '0 B'
    power = 2**10
    n = 0
    power_labels = {0 : '', 1: 'K', 2: 'M', 3: 'G', 4: 'T'}
    while size > power:
        size /= power
        n += 1
    return f"{size:.2f} {power_labels[n]}B"

async def safe_copy_to_clipboard(text):
    safe_text = json.dumps(text).replace('"', '\\"')
    js_code = f"""
    (async () => {{
        const text = {json.dumps(text)};
        try {{
            await navigator.clipboard.writeText(text);
            return true;
        }} catch (err) {{
            // 降级方案：创建 textarea
            const textArea = document.createElement("textarea");
            textArea.value = text;
            textArea.style.position = "fixed";
            document.body.appendChild(textArea);
            textArea.focus();
            textArea.select();
            try {{
                document.execCommand('copy');
                document.body.removeChild(textArea);
                return true;
            }} catch (err2) {{
                document.body.removeChild(textArea);
                return false;
            }}
        }}
    }})()
    """
    try:
        result = await ui.run_javascript(js_code)
        if result: safe_notify('已复制到剪贴板', 'positive')
        else: safe_notify('复制失败', 'negative')
    except: safe_notify('复制功能不可用 (请检查HTTPS)', 'negative')

def is_mobile_device(request) -> bool:
    """简单判断是否移动端"""
    if not request: return False
    ua = request.headers.get('user-agent', '').lower()
    keywords = ['android', 'iphone', 'ipad', 'mobile', 'harmonyos']
    return any(k in ua for k in keywords)