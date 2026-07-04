# qbot 容器镜像。用于云服务器可复现部署。
FROM python:3.11-slim

# 时区设为上海，日志时间与 A股/你本地一致
ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 先装依赖（利用 Docker 层缓存）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 再拷代码
COPY . .

# 面板端口（容器内绑 0.0.0.0，宿主机只映射到 127.0.0.1，见 compose）
ENV QBOT_HOST=0.0.0.0 QBOT_PORT=8000
EXPOSE 8000

# 默认起面板；盯盘/交易由 docker-compose 覆盖 command 起各自进程
CMD ["python", "run_dashboard.py"]
