#!/bin/bash
# 整合部署脚本：Twitter Monitor + Logo Agent
# 域名: https://monitor.dailyxdigest.uk/

set -e

echo "=========================================="
echo "整合部署：Twitter Monitor + Logo Agent"
echo "=========================================="
echo ""

# ============================================
# Part 1: 部署 Twitter Monitor
# ============================================

echo "📦 Part 1: 部署 Twitter Monitor..."
echo ""

# 1. 安装基础依赖
apt update
apt install -y python3 python3-pip python3-venv git nginx supervisor docker.io docker-compose

# 2. 部署 Twitter Monitor
mkdir -p /var/www/twitter-monitor
cd /var/www/twitter-monitor

if [ -d ".git" ]; then
    git pull origin main
else
    git clone https://github.com/Nami3Piece/twitter-monitor.git .
fi

python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# 初始化数据库
mkdir -p data
python3 -c "from db.database import init_db; import asyncio; asyncio.run(init_db())" || echo "数据库已存在"

# 配置 Supervisor
cat > /etc/supervisor/conf.d/twitter-monitor.conf << 'EOF'
[program:twitter-monitor-web]
command=/var/www/twitter-monitor/venv/bin/python3 /var/www/twitter-monitor/web.py
directory=/var/www/twitter-monitor
user=root
autostart=true
autorestart=true
stderr_logfile=/var/log/twitter-monitor-web.err.log
stdout_logfile=/var/log/twitter-monitor-web.out.log

[program:twitter-monitor-main]
command=/var/www/twitter-monitor/venv/bin/python3 /var/www/twitter-monitor/main.py
directory=/var/www/twitter-monitor
user=root
autostart=true
autorestart=true
stderr_logfile=/var/log/twitter-monitor-main.err.log
stdout_logfile=/var/log/twitter-monitor-main.out.log
EOF

supervisorctl reread
supervisorctl update

echo "✓ Twitter Monitor 部署完成"
echo ""

# ============================================
# Part 2: 部署 Logo Agent
# ============================================

echo "📦 Part 2: 部署 Logo Agent..."
echo ""

mkdir -p /var/www/logo-agent
cd /var/www/logo-agent

if [ -d ".git" ]; then
    git pull origin main
else
    git clone https://github.com/Nami3Piece/logo-agent.git .
fi

# 修改 Logo Agent 的端口为 8001
sed -i 's/8000:8000/8001:8000/g' docker-compose.yml || true

# 启动 Logo Agent
docker-compose up -d --build

echo "✓ Logo Agent 部署完成"
echo ""

# ============================================
# Part 3: 配置 Nginx 整合
# ============================================

echo "⚙️  Part 3: 配置 Nginx..."
echo ""

cat > /etc/nginx/sites-available/monitor.dailyxdigest.uk << 'EOF'
server {
    listen 80;
    server_name monitor.dailyxdigest.uk;

    # Twitter Monitor - 主应用
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Logo Agent - 子路径
    location /logo/ {
        proxy_pass http://127.0.0.1:8001/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # 处理文件上传
        client_max_body_size 500M;
        proxy_request_buffering off;
    }

    # Logo Agent 静态文件
    location /logo/static/ {
        proxy_pass http://127.0.0.1:8001/static/;
    }
}
EOF

ln -sf /etc/nginx/sites-available/monitor.dailyxdigest.uk /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx

echo "✓ Nginx 配置完成"
echo ""

# ============================================
# Part 4: 配置 SSL
# ============================================

echo "🔒 Part 4: 配置 SSL..."
echo ""

if command -v certbot &> /dev/null; then
    certbot --nginx -d monitor.dailyxdigest.uk --non-interactive --agree-tos --email noreply@dailyxdigest.uk || echo "SSL 配置失败"
else
    echo "未安装 certbot，跳过 SSL"
    echo "安装命令: apt install certbot python3-certbot-nginx"
fi

echo ""
echo "=========================================="
echo "✅ 整合部署完成！"
echo "=========================================="
echo ""
echo "访问地址:"
echo "  主站: https://monitor.dailyxdigest.uk/"
echo "  Logo Agent: https://monitor.dailyxdigest.uk/logo/"
echo ""
echo "⚠️  下一步:"
echo "1. 上传 Twitter Monitor 的 .env 到 /var/www/twitter-monitor/.env"
echo "2. 上传 Logo Agent 的 .env 到 /var/www/logo-agent/.env"
echo "3. 重启服务:"
echo "   supervisorctl restart all"
echo "   cd /var/www/logo-agent && docker-compose restart"
echo ""
echo "查看日志:"
echo "  Twitter Monitor: tail -f /var/log/twitter-monitor-web.out.log"
echo "  Logo Agent: cd /var/www/logo-agent && docker-compose logs -f"
echo ""
