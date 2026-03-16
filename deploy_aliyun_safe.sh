#!/bin/bash
# Twitter Monitor 阿里云部署脚本
# 服务器IP: 43.103.0.20

set -e

echo "=========================================="
echo "Twitter Monitor 自动部署脚本"
echo "=========================================="
echo ""

# 1. 更新系统
echo "📦 更新系统包..."
apt update && apt upgrade -y

# 2. 安装必要软件
echo "📦 安装 Python 3.9+ 和依赖..."
apt install -y python3 python3-pip python3-venv git nginx supervisor

# 3. 创建应用目录
echo "📁 创建应用目录..."
mkdir -p /var/www/twitter-monitor
cd /var/www/twitter-monitor

# 4. 克隆代码
echo "📥 从 GitHub 克隆代码..."
if [ -d ".git" ]; then
    echo "代码已存在，执行 git pull..."
    git pull origin main
else
    git clone https://github.com/Nami3Piece/twitter-monitor.git .
fi

# 5. 创建虚拟环境
echo "🐍 创建 Python 虚拟环境..."
python3 -m venv venv
source venv/bin/activate

# 6. 安装依赖
echo "📦 安装 Python 依赖..."
pip install --upgrade pip
pip install -r requirements.txt

# 7. 配置环境变量
echo "⚙️  配置环境变量..."
echo "⚠️  请手动配置 .env 文件，从本地复制或参考 .env.example"
echo "   位置: /var/www/twitter-monitor/.env"

# 8. 初始化数据库
echo "💾 初始化数据库..."
mkdir -p data
python3 -c "from db.database import init_db; import asyncio; asyncio.run(init_db())" || echo "数据库已存在"

# 9. 配置 Supervisor
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

# 10. 配置 Nginx
echo "⚙️  配置 Nginx..."
cat > /etc/nginx/sites-available/twitter-monitor << 'EOF'
server {
    listen 80;
    server_name 43.103.0.20;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF

ln -sf /etc/nginx/sites-available/twitter-monitor /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

# 11. 重启服务
echo "🔄 重启服务..."
supervisorctl reread
supervisorctl update
supervisorctl restart all
nginx -t && systemctl restart nginx

echo ""
echo "=========================================="
echo "✅ 部署完成！"
echo "=========================================="
echo ""
echo "⚠️  下一步："
echo "1. 上传 .env 文件到 /var/www/twitter-monitor/.env"
echo "2. 重启服务: supervisorctl restart all"
echo ""
echo "访问地址: http://43.103.0.20"
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
