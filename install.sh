#!/bin/bash

# ==============================================================================
# X-Fusion Panel 一键安装/管理脚本 (Git 仓库版)
# GitHub: https://github.com/SIJULY/x-fusion-panel
# ==============================================================================

# --- 全局变量 ---
PROJECT_NAME="x-fusion-panel"
INSTALL_DIR="/root/${PROJECT_NAME}"
OLD_INSTALL_DIR="/root/xui_manager" 

# 你的仓库地址
REPO_URL="https://github.com/SIJULY/x-fusion-panel.git"

# Caddy 配置标记
CADDY_MARK_START="# X-Fusion Panel Config Start"
CADDY_MARK_END="# X-Fusion Panel Config End"

# 颜色定义
RED="\033[31m"
GREEN="\033[32m"
YELLOW="\033[33m"
BLUE="\033[34m"
PLAIN="\033[0m"

# --- 辅助函数 ---
print_info() { echo -e "${BLUE}[信息]${PLAIN} $1"; }
print_success() { echo -e "${GREEN}[成功]${PLAIN} $1"; }
print_warning() { echo -e "${YELLOW}[警告]${PLAIN} $1"; }
print_error() { echo -e "${RED}[错误]${PLAIN} $1"; exit 1; }

check_root() {
    if [ "$(id -u)" -ne 0 ]; then
        print_error "此脚本必须以 root 用户身份运行。"
    fi
}

wait_for_apt_lock() {
    echo -e "${BLUE}[信息] 正在检查系统 APT 锁状态...${PLAIN}"
    local wait_time=0
    local timeout=60
    while fuser /var/lib/dpkg/lock >/dev/null 2>&1 || \
          fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 || \
          fuser /var/lib/apt/lists/lock >/dev/null 2>&1 ; do
        echo -e "${YELLOW}[警告] 系统后台正在运行更新进程，已等待 ${wait_time} 秒...${PLAIN}"
        sleep 10
        ((wait_time+=10))
        if [ "$wait_time" -ge "$timeout" ]; then
            echo -e "${RED}[错误] APT 锁已被占用超过 ${timeout} 秒。${PLAIN}"
            read -p "是否尝试强制结束占用进程并删除锁文件？(y/n) [推荐先选 n]: " force_unlock
            if [ "$force_unlock" == "y" ]; then
                echo -e "${RED}[警告] 正在执行强制解锁...${PLAIN}"
                killall apt apt-get dpkg 2>/dev/null
                rm -f /var/lib/apt/lists/lock /var/cache/apt/archives/lock /var/lib/dpkg/lock*
                dpkg --configure -a
                echo -e "${GREEN}[成功] 已执行强制清理。${PLAIN}"
                break
            else
                wait_time=0
            fi
        fi
    done
}

check_dependencies() {
    # 检查并安装 Docker
    if ! command -v docker &> /dev/null; then
        print_info "未检测到 Docker，正在安装..."
        wait_for_apt_lock
        curl -fsSL https://get.docker.com | bash
        systemctl enable docker
        systemctl start docker
    fi
    # 检查并安装 Git (这是新版必须的)
    if ! command -v git &> /dev/null; then
        print_info "未检测到 Git，正在安装..."
        wait_for_apt_lock
        if [ -f /etc/debian_version ]; then
            apt-get update && apt-get install -y git
        elif [ -f /etc/redhat-release ]; then
            yum install -y git
        fi
    fi
}

# --- 核心功能函数 ---

migrate_old_data() {
    if [ -d "$OLD_INSTALL_DIR" ] && [ ! -d "$INSTALL_DIR" ]; then
        echo -e "${YELLOW}=================================================${PLAIN}"
        print_info "检测到旧版安装目录 ($OLD_INSTALL_DIR)"
        print_info "正在自动迁移数据到新目录 ($INSTALL_DIR)..."
        
        cd "$OLD_INSTALL_DIR"
        if docker compose ps | grep -q "xui_manager"; then
            print_info "停止旧版容器..."
            docker compose down
        fi
        
        cd /root
        mv "$OLD_INSTALL_DIR" "$INSTALL_DIR"
        print_success "目录重命名完成。"
        echo -e "${YELLOW}=================================================${PLAIN}"
    fi
}

deploy_code() {
    check_dependencies
    migrate_old_data

    # 创建或更新代码库
    if [ -d "${INSTALL_DIR}/.git" ]; then
        print_info "检测到现有仓库，正在执行 Git Pull 更新..."
        cd "${INSTALL_DIR}"
        # 强制重置本地修改，确保与远程同步
        git fetch --all
        git reset --hard origin/main
        git pull
    else
        print_info "正在克隆代码仓库..."
        # 如果目录存在但不是git库(比如旧版残留)，先备份数据清理目录
        if [ -d "${INSTALL_DIR}" ]; then
            print_warning "目录存在但非Git仓库，正在备份数据并重新克隆..."
            mkdir -p /tmp/x_fusion_backup
            cp -r ${INSTALL_DIR}/data /tmp/x_fusion_backup/ 2>/dev/null
            cp ${INSTALL_DIR}/Caddyfile /tmp/x_fusion_backup/ 2>/dev/null
            rm -rf "${INSTALL_DIR}"
        fi
        
        git clone "${REPO_URL}" "${INSTALL_DIR}"
        
        # 恢复备份
        if [ -d "/tmp/x_fusion_backup/data" ]; then
            mkdir -p "${INSTALL_DIR}/data"
            cp -r /tmp/x_fusion_backup/data/* "${INSTALL_DIR}/data/"
            print_success "旧数据已恢复"
        fi
        if [ -f "/tmp/x_fusion_backup/Caddyfile" ]; then
            cp /tmp/x_fusion_backup/Caddyfile "${INSTALL_DIR}/"
        fi
    fi

    cd "${INSTALL_DIR}"
    
    # 确保 data 目录存在
    mkdir -p data
    
    # 初始化空文件 (防止挂载报错)
    if [ ! -f "data/servers.json" ]; then echo "[]" > data/servers.json; fi
    if [ ! -f "data/subscriptions.json" ]; then echo "[]" > data/subscriptions.json; fi
    if [ ! -f "data/admin_config.json" ]; then echo "{}" > data/admin_config.json; fi
    if [ ! -f "Caddyfile" ]; then touch Caddyfile; fi
}

# --- 动态生成 Docker Compose ---
generate_compose() {
    local BIND_IP=$1
    local PORT=$2
    local USER=$3
    local PASS=$4
    local SECRET=$5 
    local ENABLE_CADDY=$6

    cat > ${INSTALL_DIR}/docker-compose.yml << EOF
version: '3.8'
services:
  x-fusion-panel:
    build: .
    container_name: x-fusion-panel
    restart: always
    ports:
      - "${BIND_IP}:${PORT}:8080"
    volumes:
      # 挂载数据目录 (持久化)
      - ./data:/app/data
      # 挂载静态资源 (以便手动修改后立即生效)
      - ./static:/app/static
      # 挂载 Timezone
      - /etc/localtime:/etc/localtime:ro
    environment:
      - TZ=Asia/Shanghai
      - XUI_USERNAME=${USER}
      - XUI_PASSWORD=${PASS}
      - XUI_SECRET_KEY=${SECRET}

  subconverter:
    image: tindy2013/subconverter:latest
    container_name: subconverter
    restart: always
    ports:
      - "127.0.0.1:25500:25500"
    environment:
      - TZ=Asia/Shanghai
EOF

    if [ "$ENABLE_CADDY" == "true" ]; then
        cat >> ${INSTALL_DIR}/docker-compose.yml << EOF

  caddy:
    image: caddy:latest
    container_name: caddy
    restart: always
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile
      - ./caddy_data:/data
    depends_on:
      - x-fusion-panel
      - subconverter
EOF
    fi
}

configure_caddy_docker() {
    local DOMAIN=$1
    local DOCKER_CADDY_FILE="${INSTALL_DIR}/Caddyfile"

    if [ ! -f "$DOCKER_CADDY_FILE" ]; then touch "$DOCKER_CADDY_FILE"; fi
    sed -i "/${CADDY_MARK_START}/,/${CADDY_MARK_END}/d" "$DOCKER_CADDY_FILE"
    if [ -s "$DOCKER_CADDY_FILE" ] && [ "$(tail -c 1 "$DOCKER_CADDY_FILE")" != "" ]; then echo "" >> "$DOCKER_CADDY_FILE"; fi

    cat >> "$DOCKER_CADDY_FILE" << EOF
${CADDY_MARK_START}
${DOMAIN} {
    encode gzip
    handle_path /convert* {
        rewrite * /sub
        reverse_proxy subconverter:25500 
    }
    handle {
        reverse_proxy x-fusion-panel:8080
    }
}
${CADDY_MARK_END}
EOF
}

# --- 菜单动作 ---

install_panel() {
    wait_for_apt_lock
    
    # 1. 拉取代码
    deploy_code

    # 读取旧配置
    local def_user="admin"
    local def_pass="admin"
    local def_key=$(cat /proc/sys/kernel/random/uuid | tr -d '-')

    if [ -f "${INSTALL_DIR}/docker-compose.yml" ]; then
        grep "XUI_USERNAME" "${INSTALL_DIR}/docker-compose.yml" &>/dev/null && def_user=$(grep "XUI_USERNAME=" "${INSTALL_DIR}/docker-compose.yml" | cut -d= -f2)
        grep "XUI_PASSWORD" "${INSTALL_DIR}/docker-compose.yml" &>/dev/null && def_pass=$(grep "XUI_PASSWORD=" "${INSTALL_DIR}/docker-compose.yml" | cut -d= -f2)
        grep "XUI_SECRET_KEY" "${INSTALL_DIR}/docker-compose.yml" &>/dev/null && def_key=$(grep "XUI_SECRET_KEY=" "${INSTALL_DIR}/docker-compose.yml" | cut -d= -f2)
    fi

    echo "------------------------------------------------"
    read -p "请设置面板登录账号 [${def_user}]: " admin_user
    admin_user=${admin_user:-$def_user}
    read -p "请设置面板登录密码 [${def_pass}]: " admin_pass
    admin_pass=${admin_pass:-$def_pass}
    read -p "按回车使用推荐密钥 [${def_key}]: " input_key
    secret_key=${input_key:-$def_key}
    echo "------------------------------------------------"

    echo "请选择访问方式："
    echo "  1) IP + 端口访问"
    echo "  2) 域名访问 (自动申请证书，全新机器推荐)"
    echo "  3) 域名访问 (共存模式，已有 Nginx/Caddy 用户推荐)"
    read -p "请输入选项 [2]: " net_choice
    net_choice=${net_choice:-2}

    if [ "$net_choice" == "1" ]; then
        read -p "请输入开放端口 [8081]: " port
        port=${port:-8081}
        generate_compose "0.0.0.0" "$port" "$admin_user" "$admin_pass" "$secret_key" "false"
        
        print_info "正在构建并启动容器..."
        docker compose up -d --build
        ip_addr=$(curl -s ifconfig.me)
        print_success "安装成功！登录地址: http://${ip_addr}:${port}"

    elif [ "$net_choice" == "3" ]; then
        read -p "请输入内部运行端口 [8081]: " port
        port=${port:-8081}
        generate_compose "127.0.0.1" "$port" "$admin_user" "$admin_pass" "$secret_key" "false"
        
        print_info "正在构建并启动容器..."
        docker compose up -d --build
        
        print_success "安装成功！(共存模式)"
        echo -e "${YELLOW}请将以下配置添加到您的主 Caddyfile/Nginx 中：${PLAIN}"
        echo "handle_path /convert* { reverse_proxy 127.0.0.1:25500 }"
        echo "handle { reverse_proxy 127.0.0.1:${port} }"

    else
        read -p "请输入您的域名: " domain
        if [ -z "$domain" ]; then print_error "域名不能为空"; fi
        port=8081
        configure_caddy_docker "$domain"
        generate_compose "127.0.0.1" "$port" "$admin_user" "$admin_pass" "$secret_key" "true"
        
        print_info "正在构建并启动容器..."
        docker compose up -d --build
        print_success "安装成功！登录地址: https://${domain}"
    fi
}

update_panel() {
    if [ ! -d "${INSTALL_DIR}" ]; then print_error "未检测到安装目录，请先执行安装。"; fi
    
    echo -e "${BLUE}=================================================${PLAIN}"
    print_info "正在更新代码..."
    deploy_code # Git Pull

    # 重新构建镜像以应用代码变更
    print_info "正在重建容器..."
    docker compose up -d --build
    print_success "更新完成！"
}

uninstall_panel() {
    read -p "确定要卸载吗？(y/n): " confirm
    if [ "$confirm" != "y" ]; then exit 0; fi
    
    if [ -d "${INSTALL_DIR}" ]; then
        cd ${INSTALL_DIR}
        docker compose down
        cd /root
        rm -rf ${INSTALL_DIR}
    fi
    print_success "卸载完成。"
}

# --- 主菜单 ---
check_root
clear
echo -e "${GREEN} X-Fusion Panel 一键管理脚本 (Git版) ${PLAIN}"
echo -e "  1. 安装面板"
echo -e "  2. 更新面板"
echo -e "  3. 卸载面板"
echo -e "  0. 退出"
read -p "请输入选项: " choice

case $choice in
    1) install_panel ;;
    2) update_panel ;;
    3) uninstall_panel ;;
    0) exit 0 ;;
    *) print_error "无效选项" ;;
esac
