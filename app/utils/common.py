import base64
import json
from urllib.parse import quote
from nicegui import ui  # ✨ 必须引用 ui 才能执行 JS


# ================= 剪贴板工具 =================

async def safe_copy_to_clipboard(text):
    """
    安全复制到剪贴板 (兼容 HTTP 环境)
    使用原生 Clipboard API，如果失败则回退到 execCommand
    """
    if not text: return

    safe_text = json.dumps(text).replace('"', '\\"')
    js_code = f"""
    (async () => {{
        const text = {json.dumps(text)};
        try {{
            await navigator.clipboard.writeText(text);
            return true;
        }} catch (err) {{
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
        if result:
            ui.notify('已复制到剪贴板', type='positive')
        else:
            ui.notify('复制失败', type='negative')
    except Exception as e:
        ui.notify(f'复制功能不可用: {e}', type='negative')


# ================= 格式化工具 =================

def format_bytes(size):
    """格式化流量单位 (B -> GB)"""
    if not size: return '0 B'
    try:
        size = float(size)
        power = 2 ** 10
        n = 0
        power_labels = {0: '', 1: 'K', 2: 'M', 3: 'G', 4: 'T'}
        while size > power:
            size /= power
            n += 1
        return f"{size:.2f} {power_labels[n]}B"
    except:
        return '0 B'


# ================= 编码工具 =================

def safe_base64(s):
    """URL安全 Base64 编码 (用于 VLESS 链接等)"""
    return base64.urlsafe_b64encode(s.encode('utf-8')).decode('utf-8')


def decode_base64_safe(s):
    """URL安全 Base64 解码 (自动补全 padding)"""
    try:
        # 兼容标准 Base64 和 URL Safe Base64
        # 补全 padding
        missing_padding = len(s) % 4
        if missing_padding: s += '=' * (4 - missing_padding)
        return base64.urlsafe_b64decode(s).decode('utf-8')
    except:
        try:
            return base64.b64decode(s).decode('utf-8')
        except:
            return ""