#!/bin/bash
# 整合部署测试脚本

echo "=========================================="
echo "测试整合部署状态"
echo "=========================================="
echo ""

# 1. 检查服务状态
echo "1️⃣ 检查服务状态..."
echo ""

echo "Twitter Monitor Web:"
supervisorctl status twitter-monitor-web

echo ""
echo "Twitter Monitor Main:"
supervisorctl status twitter-monitor-main

echo ""
echo "Logo Agent Docker:"
cd /var/www/logo-agent && docker-compose ps

echo ""

# 2. 检查端口监听
echo "2️⃣ 检查端口监听..."
echo ""

echo "端口 8000 (Twitter Monitor):"
netstat -tlnp | grep :8000 || ss -tlnp | grep :8000

echo ""
echo "端口 8001 (Logo Agent):"
netstat -tlnp | grep :8001 || ss -tlnp | grep :8001

echo ""

# 3. 测试本地访问
echo "3️⃣ 测试本地访问..."
echo ""

echo "Twitter Monitor (http://127.0.0.1:8000):"
curl -s -o /dev/null -w "HTTP Status: %{http_code}\n" http://127.0.0.1:8000

echo ""
echo "Logo Agent (http://127.0.0.1:8001):"
curl -s -o /dev/null -w "HTTP Status: %{http_code}\n" http://127.0.0.1:8001

echo ""

# 4. 检查 Nginx 配置
echo "4️⃣ 检查 Nginx 配置..."
echo ""

nginx -t

echo ""

# 5. 测试域名访问
echo "5️⃣ 测试域名访问..."
echo ""

echo "主站 (https://monitor.dailyxdigest.uk/):"
curl -s -o /dev/null -w "HTTP Status: %{http_code}\n" https://monitor.dailyxdigest.uk/

echo ""
echo "Logo Agent (https://monitor.dailyxdigest.uk/logo/):"
curl -s -o /dev/null -w "HTTP Status: %{http_code}\n" https://monitor.dailyxdigest.uk/logo/

echo ""

# 6. 检查日志
echo "6️⃣ 最近的错误日志..."
echo ""

echo "Twitter Monitor Web 错误:"
tail -5 /var/log/twitter-monitor-web.err.log 2>/dev/null || echo "无错误日志"

echo ""
echo "Logo Agent 日志:"
cd /var/www/logo-agent && docker-compose logs --tail=5 backend 2>/dev/null || echo "无法获取日志"

echo ""
echo "=========================================="
echo "测试完成！"
echo "=========================================="
echo ""
echo "如果所有状态都是 RUNNING 且 HTTP 状态都是 200，"
echo "说明部署成功！"
echo ""
