#!/bin/bash
# 完成 Twitter Monitor 部署

set -e

echo "=========================================="
echo "完成 Twitter Monitor 部署"
echo "=========================================="
echo ""

# 检查是否为 root
if [ "$EUID" -ne 0 ]; then
    echo "❌ 请使用 sudo 执行"
    exit 1
fi

# 1. 启动 Nginx
echo "1️⃣ 启动 Nginx..."
systemctl start nginx
systemctl enable nginx
echo "✓ Nginx 已启动"
echo ""

# 2. 检查 .env 文件
echo "2️⃣ 检查 .env 文件..."
if [ ! -f /var/www/twitter-monitor/.env ]; then
    echo "⚠️  .env 文件不存在"
    echo "请在本地 Mac 执行："
    echo "  scp /Users/namipieces/twitter-monitor/.env admin@43.103.0.20:/tmp/.env"
    echo ""
    echo "然后在服务器执行："
    echo "  sudo mv /tmp/.env /var/www/twitter-monitor/.env"
    echo ""
    read -p "按回车继续（如果已上传 .env）或 Ctrl+C 退出..."
else
    echo "✓ .env 文件已存在"
fi
echo ""

# 3. 重启服务
echo "3️⃣ 重启 Twitter Monitor 服务..."
supervisorctl restart all
echo ""

# 4. 检查服务状态
echo "4️⃣ 检查服务状态..."
supervisorctl status
echo ""

# 5. 检查端口
echo "5️⃣ 检查端口监听..."
netstat -tlnp | grep :8000 || ss -tlnp | grep :8000 || echo "端口 8000 未监听"
echo ""

# 6. 测试本地访问
echo "6️⃣ 测试本地访问..."
sleep 2
curl -s -o /dev/null -w "HTTP Status: %{http_code}\n" http://127.0.0.1:8000 || echo "无法访问"
echo ""

# 7. 配置 SSL（如果需要）
echo "7️⃣ 配置 SSL..."
if command -v certbot &> /dev/null; then
    certbot --nginx -d monitor.dailyxdigest.uk --non-interactive --agree-tos --email noreply@dailyxdigest.uk || echo "SSL 配置失败"
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
echo "  tail -f /var/log/twitter-monitor-main.out.log"
echo ""
echo "管理服务:"
echo "  supervisorctl status"
echo "  supervisorctl restart all"
echo ""
