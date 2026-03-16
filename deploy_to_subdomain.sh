#!/bin/bash
# Twitter Monitor 部署到现有阿里云服务器
# 域名: https://monitor.dailyxdigest.uk/

set -e

echo "=========================================="
echo "Twitter Monitor 部署脚本"
echo "部署到: https://monitor.dailyxdigest.uk/"
echo "=========================================="
echo ""

# 1. 安装必要软件（如果还没有）
echo "📦 检查并安装依赖..."
apt update
apt install -y python3 python3-pip python3-venv git supervisor

# 2. 创建应用目录
echo "📁 创建应用目录..."
mkdir -p /var/www/twitter-monitor
cd /var/www/twitter-monitor

# 3. 克隆或更新代码
echo "📥 从 GitHub 获取最新代码..."
if [ -d ".git" ]; then
    echo "代码已存在，执行 git pull..."
    git pull origin main
else
    git clone https://github.com/Nami3Piece/twitter-monitor.git .
fi

# 4. 创建虚拟环境
echo "🐍 创建 Python 虚拟环境..."
python3 -m venv venv
source venv/bin/activate

# 5. 安装依赖
echo "📦 安装 Python 依赖..."
pip install --upgrade pip
pip install -r requirements.txt

# 6. 配置环境变量
echo "⚙️  配置环境变量..."
echo "⚠️  请手动上传 .env 文件到 /var/www/twitter-monitor/.env"

# 7. 初始化数据库
echo "💾 初始化数据库..."
mkdir -p data
python3 -c "from db.database import init_db; import asyncio; asyncio.run(init_db())" || echo "数据库已存在"

# 8. 配置 Supervisor
echo "⚙️  配置 Supervisor..."
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

# 9. 配置 Nginx（添加新的 server block）
echo "⚙️  配置 Nginx..."
cat > /etc/nginx/sites-available/twitter-monitor << 'EOF'
server {
    listen 80;
    server_name monitor.dailyxdigest.uk;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF

# 启用站点
ln -sf /etc/nginx/sites-available/twitter-monitor /etc/nginx/sites-enabled/

# 10. 配置 SSL（如果有 certbot）
echo "🔒 配置 SSL..."
if command -v certbot &> /dev/null; then
    echo "检测到 certbot，配置 HTTPS..."
    certbot --nginx -d monitor.dailyxdigest.uk --non-interactive --agree-tos --email noreply@dailyxdigest.uk || echo "SSL 配置失败，请手动配置"
else
    echo "未检测到 certbot，跳过 SSL 配置"
    echo "如需 HTTPS，请安装: apt install certbot python3-certbot-nginx"
fi

# 11. 重启服务
echo "🔄 重启服务..."
supervisorctl reread
supervisorctl update
supervisorctl restart all
nginx -t && systemctl reload nginx

echo ""
echo "=========================================="
echo "✅ 部署完成！"
echo "=========================================="
echo ""
echo "⚠️  下一步："
echo "1. 上传 .env 文件到 /var/www/twitter-monitor/.env"
echo "2. 确保 DNS 已配置: monitor.dailyxdigest.uk -> 43.103.0.20"
echo "3. 重启服务: supervisorctl restart all"
echo ""
echo "访问地址: https://monitor.dailyxdigest.uk/"
echo ""
echo "查看日志:"
echo "  Web服务: tail -f /var/log/twitter-monitor-web.out.log"
echo "  监控服务: tail -f /var/log/twitter-monitor-main.out.log"
echo ""
echo "管理服务:"
echo "  supervisorctl status"
echo "  supervisorctl restart twitter-monitor-web"
echo "  supervisorctl restart twitter-monitor-main"
echo ""
