FROM python:3.11-slim

WORKDIR /app

# 设置时区
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 安装基础系统依赖 (Ping, Curl, Git等)
RUN apt-get update && apt-get install -y iputils-ping curl git && rm -rf /var/lib/apt/lists/*

# 1. 先复制依赖文件 (利用 Docker 缓存层，加速构建)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 2. 复制项目所有源代码
# 注意：这会将 api/, core/, services/, ui/, static/, main.py 等全部复制进去
COPY . .

# 3. 启动命令 (main.py 现在在根目录，不是 app/main.py 了)
CMD ["python", "main.py"]
