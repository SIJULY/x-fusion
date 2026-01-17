# ui/assets.py

# ================= 头部注入 (CSS/JS) =================
COMMON_HEAD_HTML = r'''
    <link rel="stylesheet" href="/static/xterm.css" />
    <script src="/static/xterm.js"></script>
    <script src="/static/xterm-addon-fit.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;700&family=Noto+Color+Emoji&display=swap" rel="stylesheet">
    <link href="https://use.fontawesome.com/releases/v6.4.0/css/all.css" rel="stylesheet">
    <style>
        @font-face {
            font-family: 'Twemoji Country Flags';
            src: url('https://cdn.jsdelivr.net/npm/country-flag-emoji-polyfill@0.1/dist/TwemojiCountryFlags.woff2') format('woff2');
            unicode-range: U+1F1E6-1F1FF, U+1F3F4, U+E0062-E007F;
        }
        body { 
            margin: 0; 
            font-family: "Twemoji Country Flags", "Noto Color Emoji", "Segoe UI Emoji", "Noto Sans SC", sans-serif; 
            background-color: #f8fafc; 
            transition: background-color 0.3s ease; 
        }
        body:not(.body--dark) { background: linear-gradient(135deg, #e0c3fc 0%, #8ec5fc 100%); }
        body.body--dark { background-color: #0b1121; }
        .nicegui-connection-lost { display: none !important; }

        /* 状态卡片样式 */
        .status-card { transition: all 0.3s ease; border-radius: 16px; }
        body:not(.body--dark) .status-card { background: rgba(255, 255, 255, 0.95); border: 1px solid rgba(255, 255, 255, 0.8); box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.1); color: #1e293b; }
        body.body--dark .status-card { background: #1e293b; border: 1px solid rgba(255,255,255,0.05); box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.3); color: #e2e8f0; }
        .status-card:hover { transform: translateY(-3px); }

        /* 滚动条隐藏 */
        .scrollbar-hide::-webkit-scrollbar { display: none; }
        .scrollbar-hide { -ms-overflow-style: none; scrollbar-width: none; }
    </style>
'''

# ================= 2D 平面地图结构 =================
GLOBE_STRUCTURE = r"""
<style>
    #earth-container { width: 100%; height: 100%; position: relative; overflow: hidden; border-radius: 12px; background-color: #100C2A; }
    .earth-stats { position: absolute; top: 20px; left: 20px; color: rgba(255, 255, 255, 0.8); font-family: 'Consolas', monospace; font-size: 12px; z-index: 10; background: rgba(0, 20, 40, 0.6); padding: 10px 15px; border: 1px solid rgba(0, 255, 255, 0.3); border-radius: 6px; backdrop-filter: blur(4px); pointer-events: none; }
    .earth-stats span { color: #00ffff; font-weight: bold; }
</style>
<div id="earth-container">
    <div class="earth-stats">
        <div>ACTIVE NODES: <span id="node-count">0</span></div>
        <div>REGIONS: <span id="region-count">0</span></div>
    </div>
    <div id="earth-render-area" style="width:100%; height:100%;"></div>
</div>
"""

# ================= 2D 平面地图 JS 逻辑 =================
GLOBE_JS_LOGIC = r"""
(function() {
    var container = document.getElementById('earth-render-area');
    if (!container) return;
    var serverData = window.DASHBOARD_DATA || [];
    var myLat = 39.9; var myLon = 116.4;
    var emojiFont = '"Twemoji Country Flags", "Noto Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol", sans-serif';

    var nodeCountEl = document.getElementById('node-count');
    var regionCountEl = document.getElementById('region-count');
    function updateStats(data) {
        if(nodeCountEl) nodeCountEl.textContent = data.length;
        const uniqueRegions = new Set(data.map(s => s.name));
        if(regionCountEl) regionCountEl.textContent = uniqueRegions.size;
    }
    updateStats(serverData);

    var existing = echarts.getInstanceByDom(container);
    if (existing) existing.dispose();
    var myChart = echarts.init(container);

    if (navigator.geolocation) {
        navigator.geolocation.getCurrentPosition(function(position) {
            myLat = position.coords.latitude;
            myLon = position.coords.longitude;
            var option = buildOption(window.cachedWorldJson, serverData, myLat, myLon);
            myChart.setOption(option);
        });
    }

    window.updateDashboardMap = function(newData) {
        if (!window.cachedWorldJson || !myChart) return;
        serverData = newData;
        updateStats(newData);
        var option = buildOption(window.cachedWorldJson, newData, myLat, myLon);
        myChart.setOption(option);
    };

    const searchKeys = {
        '🇺🇸': 'United States', '🇨🇳': 'China', '🇭🇰': 'China', '🇹🇼': 'China', '🇯🇵': 'Japan', '🇰🇷': 'Korea',
        '🇸🇬': 'Singapore', '🇬🇧': 'United Kingdom', '🇩🇪': 'Germany', '🇫🇷': 'France', '🇷🇺': 'Russia',
        '🇨🇦': 'Canada', '🇦🇺': 'Australia', '🇮🇳': 'India', '🇧🇷': 'Brazil'
    };

    function buildOption(mapGeoJSON, data, userLat, userLon) {
        const mapFeatureNames = mapGeoJSON.features.map(f => f.properties.name);
        const activeMapNames = new Set();

        data.forEach(s => {
            let keyword = null;
            for (let key in searchKeys) {
                if ((s.name && s.name.includes(key))) { keyword = searchKeys[key]; break; }
            }
            if (keyword && mapFeatureNames.includes(keyword)) { activeMapNames.add(keyword); }
        });

        const highlightRegions = Array.from(activeMapNames).map(name => ({
            name: name,
            itemStyle: { areaColor: '#0055ff', borderColor: '#00ffff', borderWidth: 1.5, opacity: 0.9 }
        }));

        const scatterData = data.map(s => ({
            name: s.name, value: [s.lon, s.lat], itemStyle: { color: '#00ffff' }
        }));

        scatterData.push({
            name: "ME", value: [userLon, userLat], itemStyle: { color: '#FFD700' },
            symbolSize: 15, label: { show: true, position: 'top', formatter: 'My PC', color: '#FFD700' }
        });

        const linesData = data.map(s => ({ coords: [[s.lon, s.lat], [userLon, userLat]] }));

        return {
            backgroundColor: '#100C2A', 
            geo: {
                map: 'world', roam: false, zoom: 1.2, center: [15, 10], label: { show: false },
                itemStyle: { areaColor: '#1B2631', borderColor: '#404a59', borderWidth: 1 },
                emphasis: { itemStyle: { areaColor: '#2a333d' }, label: { show: false } },
                regions: highlightRegions 
            },
            series: [
                {
                    type: 'lines', coordinateSystem: 'geo', zlevel: 2,
                    effect: { show: true, period: 4, trailLength: 0.5, color: '#00ffff', symbol: 'arrow', symbolSize: 6 },
                    lineStyle: { color: '#00ffff', width: 1, opacity: 0, curveness: 0.2 },
                    data: linesData
                },
                {
                    type: 'scatter', coordinateSystem: 'geo', zlevel: 3, symbol: 'circle', symbolSize: 12,
                    itemStyle: { color: '#00ffff', shadowBlur: 10, shadowColor: '#333' },
                    label: { show: true, position: 'right', formatter: '{b}', color: '#fff', fontSize: 16, fontWeight: 'bold', fontFamily: emojiFont },
                    data: scatterData
                }
            ]
        };
    }

    fetch('/static/world.json').then(r => r.json()).then(worldJson => {
        echarts.registerMap('world', worldJson);
        window.cachedWorldJson = worldJson;
        var option = buildOption(worldJson, serverData, myLat, myLon);
        myChart.setOption(option);
        window.addEventListener('resize', () => myChart.resize());
        new ResizeObserver(() => myChart.resize()).observe(container);
    });
})();
"""