# core/config.py
import os
import sys

# 🛑 [修改这里]：自动适配本地环境，不再强制使用 /app/data
# 获取当前脚本所在目录的绝对路径
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')

# 打印调试信息
print(f"🔒 [System] 数据存储目录: {DATA_DIR}")

# 确保数据目录存在
if not os.path.exists(DATA_DIR):
    try:
        os.makedirs(DATA_DIR)
    except Exception as e:
        print(f"❌ 严重错误: 无法创建数据目录 {DATA_DIR}: {e}")
        # 如果无法创建，回退到临时目录防止崩溃
        import tempfile
        DATA_DIR = os.path.join(tempfile.gettempdir(), 'x-fusion-data')
        print(f"⚠️以此回退到临时目录: {DATA_DIR}")
        if not os.path.exists(DATA_DIR): os.makedirs(DATA_DIR)

# 定义文件路径
CONFIG_FILE = os.path.join(DATA_DIR, 'servers.json')
SUBS_FILE = os.path.join(DATA_DIR, 'subscriptions.json')
NODES_CACHE_FILE = os.path.join(DATA_DIR, 'nodes_cache.json')
ADMIN_CONFIG_FILE = os.path.join(DATA_DIR, 'admin_config.json')
GLOBAL_SSH_KEY_FILE = os.path.join(DATA_DIR, 'global_ssh_key')

# 环境变量 (带默认值)
AUTO_REGISTER_SECRET = os.getenv('XUI_SECRET_KEY', 'sijuly_secret_key_default')
ADMIN_USER = os.getenv('XUI_USERNAME', 'admin')
ADMIN_PASS = os.getenv('XUI_PASSWORD', 'admin')

# 智能分组映射表 (国家/地区关键词)
AUTO_COUNTRY_MAP = {
    # --- 亚太地区 ---
    '🇨🇳': '🇨🇳 中国', 'China': '🇨🇳 中国', '中国': '🇨🇳 中国', 'CN': '🇨🇳 中国', 'PRC': '🇨🇳 中国',
    '🇭🇰': '🇭🇰 香港', 'HK': '🇭🇰 香港', 'Hong Kong': '🇭🇰 香港',
    '🇲🇴': '🇲🇴 澳门', 'MO': '🇲🇴 澳门', 'Macau': '🇲🇴 澳门',
    '🇹🇼': '🇹🇼 台湾', 'TW': '🇹🇼 台湾', 'Taiwan': '🇹🇼 台湾',
    '🇯🇵': '🇯🇵 日本', 'JP': '🇯🇵 日本', 'Japan': '🇯🇵 日本', 'Tokyo': '🇯🇵 日本', 'Osaka': '🇯🇵 日本',
    '🇸🇬': '🇸🇬 新加坡', 'SG': '🇸🇬 新加坡', 'Singapore': '🇸🇬 新加坡',
    '🇰🇷': '🇰🇷 韩国', 'KR': '🇰🇷 韩国', 'Korea': '🇰🇷 韩国', 'Seoul': '🇰🇷 韩国',
    '🇮🇳': '🇮🇳 印度', 'IN': '🇮🇳 印度', 'India': '🇮🇳 印度',
    '🇮🇩': '🇮🇩 印度尼西亚', 'ID': '🇮🇩 印度尼西亚', 'Indonesia': '🇮🇩 印度尼西亚',
    '🇲🇾': '🇲🇾 马来西亚', 'MY': '🇲🇾 马来西亚', 'Malaysia': '🇲🇾 马来西亚',
    '🇹🇭': '🇹🇭 泰国', 'TH': '🇹🇭 泰国', 'Thailand': '🇹🇭 泰国', 'Bangkok': '🇹🇭 泰国',
    '🇻🇳': '🇻🇳 越南', 'VN': '🇻🇳 越南', 'Vietnam': '🇻🇳 越南',
    '🇵🇭': '🇵🇭 菲律宾', 'PH': '🇵🇭 菲律宾', 'Philippines': '🇵🇭 菲律宾',
    '🇦🇺': '🇦🇺 澳大利亚', 'AU': '🇦🇺 澳大利亚', 'Australia': '🇦🇺 澳大利亚', 'Sydney': '🇦🇺 澳大利亚',

    # --- 北美地区 ---
    '🇺🇸': '🇺🇸 美国', 'USA': '🇺🇸 美国', 'US': '🇺🇸 美国', 'United States': '🇺🇸 美国', 'America': '🇺🇸 美国',
    '🇨🇦': '🇨🇦 加拿大', 'CA': '🇨🇦 加拿大', 'Canada': '🇨🇦 加拿大',
    '🇲🇽': '🇲🇽 墨西哥', 'MX': '🇲🇽 墨西哥', 'Mexico': '🇲🇽 墨西哥',

    # --- 南美地区 ---
    '🇧🇷': '🇧🇷 巴西', 'BR': '🇧🇷 巴西', 'Brazil': '🇧🇷 巴西',
    '🇨🇱': '🇨🇱 智利', 'CL': '🇨🇱 智利', 'Chile': '🇨🇱 智利',
    '🇦🇷': '🇦🇷 阿根廷', 'AR': '🇦🇷 阿根廷', 'Argentina': '🇦🇷 阿根廷',

    # --- 欧洲地区 ---
    '🇬🇧': '🇬🇧 英国', 'UK': '🇬🇧 英国', 'GB': '🇬🇧 英国', 'United Kingdom': '🇬🇧 英国', 'London': '🇬🇧 英国',
    '🇩🇪': '🇩🇪 德国', 'DE': '🇩🇪 德国', 'Germany': '🇩🇪 德国', 'Frankfurt': '🇩🇪 德国',
    '🇫🇷': '🇫🇷 法国', 'FR': '🇫🇷 法国', 'France': '🇫🇷 法国', 'Paris': '🇫🇷 法国',
    '🇳🇱': '🇳🇱 荷兰', 'NL': '🇳🇱 荷兰', 'Netherlands': '🇳🇱 荷兰', 'Amsterdam': '🇳🇱 荷兰',
    '🇷🇺': '🇷🇺 俄罗斯', 'RU': '🇷🇺 俄罗斯', 'Russia': '🇷🇺 俄罗斯', 'Moscow': '🇷🇺 俄罗斯',
    '🇮🇹': '🇮🇹 意大利', 'IT': '🇮🇹 意大利', 'Italy': '🇮🇹 意大利',
    '🇪🇸': '🇪🇸 西班牙', 'ES': '🇪🇸 西班牙', 'Spain': '🇪🇸 西班牙',
    '🇹🇷': '🇹🇷 土耳其', 'TR': '🇹🇷 土耳其', 'Turkey': '🇹🇷 土耳其',

    # --- 中东与非洲 ---
    '🇦🇪': '🇦🇪 阿联酋', 'AE': '🇦🇪 阿联酋', 'UAE': '🇦🇪 阿联酋', 'Dubai': '🇦🇪 阿联酋',
    '🇮🇱': '🇮🇱 以色列', 'IL': '🇮🇱 以色列', 'Israel': '🇮🇱 以色列',
    '🇿🇦': '🇿🇦 南非', 'ZA': '🇿🇦 南非', 'South Africa': '🇿🇦 南非',
    '🇸🇦': '🇸🇦 沙特', 'SA': '🇸🇦 沙特', 'Saudi Arabia': '🇸🇦 沙特',
    '🇮🇷': '🇮🇷 伊朗', 'IR': '🇮🇷 伊朗', 'Iran': '🇮🇷 伊朗',
    '🇪🇬': '🇪🇬 埃及', 'EG': '🇪🇬 埃及', 'Egypt': '🇪🇬 埃及',
    '🇳🇬': '🇳🇬 尼日利亚', 'NG': '🇳🇬 尼日利亚', 'Nigeria': '🇳🇬 尼日利亚',
}