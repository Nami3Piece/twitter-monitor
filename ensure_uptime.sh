#!/bin/bash
# 配置服务自动重启和长期在线

set -e

echo "=========================================="
echo "配置服务长期在线"
echo "=========================================="
echo ""

# 1. 确保 Supervisor 开机自启
echo "1️⃣ 配置 Supervisor 开机自启..."
systemctl enable supervisor
systemctl start supervisor

# 2. 确保 Docker 开机自启
echo "2️⃣ 配置 Docker 开机自启..."
systemctl enable docker
systemctl start docker

# 3. 修改 Logo Agent 的 docker-compose.yml，添加重启策略
echo "3️⃣ 配置 Logo Agent 自动重启..."
cd /var/www/logo-agent

# 备份原文件
cp docker-compose.yml docker-compose.yml.bak

# 为所有服务添加 restart: always
cat > docker-compose.yml << 'EOF'
version: '3.8'

services:
  redis:
    image: redis:7-alpine
    restart: always
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data

  backend:
    build: ./backend
    restart: always
    ports:
      - "8001:8000"
    volumes:
      - ./backend:/app
      - ./uploads:/app/uploads
    environment:
      - REDIS_URL=redis://redis:6379/0
    depends_on:
      - redis

  worker-pdf:
    build: ./backend
    restart: always
    command: celery -A app.workers.celery_app worker --loglevel=info -Q pdf --concurrency=2
    volumes:
      - ./backend:/app
      - ./uploads:/app/uploads
    environment:
      - REDIS_URL=redis://redis:6379/0
    depends_on:
      - redis

  worker-video:
    build: ./backend
    restart: always
    command: celery -A app.workers.celery_app worker --loglevel=info -Q video --concurrency=1
    volumes:
      - ./backend:/app
      - ./uploads:/app/uploads
    environment:
      - REDIS_URL=redis://redis:6379/0
    depends_on:
      - redis

volumes:
  redis_data:
EOF

# 重启 Logo Agent
docker-compose down
docker-compose up -d --build

echo "✓ Logo Agent 已配置自动重启"
echo ""

# 4. 配置 Nginx 开机自启
echo "4️⃣ 配置 Nginx 开机自启..."
systemctl enable nginx
systemctl start nginx

echo "✓ Nginx 已配置开机自启"
echo ""

# 5. 创建监控脚本（可选）
echo "5️⃣ 创建服务监控脚本..."
cat > /usr/local/bin/monitor-services.sh << 'EOF'
#!/bin/bash
# 服务监控脚本

# 检查 Twitter Monitor
if ! supervisorctl status twitter-monitor-web | grep -q RUNNING; then
    echo "Twitter Monitor Web 未运行，正在重启..."
    supervisorctl restart twitter-monitor-web
fi

if ! supervisorctl status twitter-monitor-main | grep -q RUNNING; then
    echo "Twitter Monitor Main 未运行，正在重启..."
    supervisorctl restart twitter-monitor-main
fi

# 检查 Logo Agent
cd /var/www/logo-agent
if ! docker-compose ps | grep -q "Up"; then
    echo "Logo Agent 未运行，正在重启..."
    docker-compose restart
fi
EOF

chmod +x /usr/local/bin/monitor-services.sh

# 添加到 crontab（每5分钟检查一次）
(crontab -l 2>/dev/null | grep -v monitor-services.sh; echo "*/5 * * * * /usr/local/bin/monitor-services.sh >> /var/log/service-monitor.log 2>&1") | crontab -

echo "✓ 服务监控脚本已创建（每5分钟检查一次）"
echo ""

# 6. 重启所有服务
echo "6️⃣ 重启所有服务..."
supervisorctl restart all
cd /var/www/logo-agent && docker-compose restart

echo ""
echo "=========================================="
echo "✅ 配置完成！"
echo "=========================================="
echo ""
echo "长期在线保障："
echo "  ✓ Supervisor 自动重启 Twitter Monitor"
echo "  ✓ Docker 自动重启 Logo Agent"
echo "  ✓ 所有服务开机自启"
echo "  ✓ 每5分钟自动检查服务状态"
echo ""
echo "查看监控日志:"
echo "  tail -f /var/log/service-monitor.log"
echo ""
