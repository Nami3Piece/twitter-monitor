#!/bin/bash
# Twitter Monitor 一键部署脚本
# 参考 Logo Agent 的成功部署经验

set -e

echo "=========================================="
echo "Twitter Monitor 一键部署"
echo "=========================================="
echo ""

# 检查 root 权限
if [ "$EUID" -ne 0 ]; then
    echo "❌ 请使用 sudo 执行此脚本"
    exit 1
fi

# 1. 检查并停止占用端口的服务
echo "1️⃣ 检查端口占用..."
if netstat -tlnp | grep -q :80 || ss -tlnp | grep -q :80; then
    echo "端口 80 已被占用，尝试停止现有服务..."
    systemctl stop nginx 2>/dev/null || true
    # 等待端口释放
    sleep 2
fi

if netstat -tlnp | grep -q :8000 || ss -tlnp | grep -q :8000; then
    echo "端口 8000 已被占用，停止相关进程..."
    supervisorctl stop all 2>/dev/null || true
    pkill -f "python.*web.py" 2>/dev/null || true
    sleep 2
fi

# 2. 安装依赖
echo "2️⃣ 安装依赖..."
apt update
apt install -y python3 python3-pip python3-venv git nginx supervisor

# 3. 部署代码
echo "3️⃣ 部署代码..."
mkdir -p /var/www/twitter-monitor
cd /var/www/twitter-monitor

if [ -d ".git" ]; then
    echo "更新现有代码..."
    git pull origin main
else
    echo "克隆代码..."
    git clone https://github.com/Nami3Piece/twitter-monitor.git .
fi

# 4. 设置 Python 环境
echo "4️⃣ 设置 Python 环境..."
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# 5. 检查 .env 文件
echo "5️⃣ 检查配置文件..."
if [ ! -f .env ]; then
    echo "⚠️  .env 文件不存在！"
    echo ""
    echo "请在本地 Mac 执行以下命令上传 .env："
    echo "  scp /Users/namipieces/twitter-monitor/.env admin@43.103.0.20:/tmp/.env"
    echo ""
    echo "然后在服务器执行："
    echo "  sudo mv /tmp/.env /var/www/twitter-monitor/.env"
    echo "  sudo /root/finish_deployment.sh"
    echo ""
    exit 1
fi

# 6. 初始化数据库
echo "6️⃣ 初始化数据库..."
mkdir -p data
python3 -c "from db.database import init_db; import asyncio; asyncio.run(init_db())" 2>/dev/null || echo "数据库已存在"

# 7. 配置 Supervisor
echo "7️⃣ 配置 Supervisor..."
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
supervisorctl restart all

# 8. 配置 Nginx
echo "8️⃣ 配置 Nginx..."

# 删除可能冲突的配置
rm -f /etc/nginx/sites-enabled/default
rm -f /etc/nginx/sites-enabled/monitor.dailyxdigest.uk

cat > /etc/nginx/sites-available/monitor.dailyxdigest.uk << 'EOF'
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

ln -sf /etc/nginx/sites-available/monitor.dailyxdigest.uk /etc/nginx/sites-enabled/

# 测试配置
nginx -t

# 启动 Nginx
systemctl restart nginx
systemctl enable nginx

# 9. 等待服务启动
echo "9️⃣ 等待服务启动..."
sleep 5

# 10. 检查服务状态
echo "🔟 检查服务状态..."
echo ""
echo "Supervisor 状态:"
supervisorctl status
echo ""

echo "端口监听:"
netstat -tlnp | grep :8000 || ss -tlnp | grep :8000 || echo "⚠️  端口 8000 未监听"
netstat -tlnp | grep :80 || ss -tlnp | grep :80 || echo "⚠️  端口 80 未监听"
echo ""

echo "本地访问测试:"
curl -s -o /dev/null -w "HTTP Status: %{http_code}\n" http://127.0.0.1:8000 || echo "⚠️  无法访问"
echo ""

# 11. 配置 SSL
echo "1️⃣1️⃣ 配置 SSL..."
if command -v certbot &> /dev/null; then
    certbot --nginx -d monitor.dailyxdigest.uk --non-interactive --agree-tos --email noreply@dailyxdigest.uk || echo "⚠️  SSL 配置失败"
else
    echo "未安装 certbot，跳过 SSL"
    echo "安装命令: apt install certbot python3-certbot-nginx"
fi

echo ""
echo "=========================================="
echo "✅ 部署完成！"
echo "=========================================="
echo ""
echo "访问地址: https://monitor.dailyxdigest.uk/"
echo ""
echo "查看日志:"
echo "  tail -f /var/log/twitter-monitor-web.out.log"
echo "  tail -f /var/log/twitter-monitor-web.err.log"
echo ""
echo "管理命令:"
echo "  supervisorctl status          # 查看状态"
echo "  supervisorctl restart all     # 重启服务"
echo "  systemctl status nginx        # 查看 Nginx"
echo ""
