# static/download_assets.py
import os
import requests

# 定义需要下载的文件清单
ASSETS = {
    "xterm.js": "https://cdn.jsdelivr.net/npm/xterm@5.3.0/lib/xterm.js",
    "xterm.css": "https://cdn.jsdelivr.net/npm/xterm@5.3.0/css/xterm.css",
    "xterm-addon-fit.js": "https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.8.0/lib/xterm-addon-fit.js",
    "world.json": "https://raw.githubusercontent.com/apache/echarts/master/test/data/map/json/world.json"
}


def download_file(url, filepath):
    print(f"⬇️ Downloading {filepath} ...")
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        with open(filepath, 'wb') as f:
            f.write(r.content)
        print(f"✅ Saved to {filepath}")
    except Exception as e:
        print(f"❌ Failed to download {url}: {e}")


if __name__ == "__main__":
    # 确保当前在 static 目录
    current_dir = os.path.dirname(os.path.abspath(__file__))

    for name, url in ASSETS.items():
        target_path = os.path.join(current_dir, name)
        if not os.path.exists(target_path):
            download_file(url, target_path)
        else:
            print(f"⏭️ {name} exists, skipping.")

    print("\n🎉 All static assets are ready!")