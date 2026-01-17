FROM python:3.11-slim

WORKDIR /app

# 设置时区
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 安装基础系统依赖
# iputils-ping: 用于 logic.py 中的 Ping 检测
# curl, git: 用于辅助功能
# procps: 用于进程管理 (可选，增强稳定性)
RUN apt-get update && apt-get install -y iputils-ping curl git procps && rm -rf /var/lib/apt/lists/*

# 1. 先复制依赖文件 (利用 Docker 缓存层，加速构建)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 2. 复制项目所有源代码
# 这会将当前目录下的所有文件（包括 main.py, config.py, data/ 等）复制到容器的 /app
COPY . .

# 3. 启动命令
# 由于 WORKDIR 是 /app，且 copy 了 . 到 .，所以 main.py 就在 /app/main.py
CMD ["python", "main.py"]
