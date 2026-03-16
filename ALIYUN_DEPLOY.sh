#!/bin/bash
# 阿里云 ECS 部署脚本

echo "🚀 阿里云 ECS 部署指南"
echo ""
echo "推荐配置："
echo "  - 实例：轻量应用服务器（最便宜）"
echo "  - 规格：1核2GB（约 ¥24/月）"
echo "  - 系统：Ubuntu 22.04"
echo "  - 地域：香港或新加坡（国际访问快）"
echo ""
echo "部署步骤："
echo ""
echo "1. 购买服务器: https://www.aliyun.com/product/swas"
echo ""
echo "2. SSH 登录服务器后，运行以下命令："
echo ""
cat << 'DEPLOY_SCRIPT'
# 安装依赖
sudo apt update
sudo apt install -y python3 python3-pip git nginx

# 克隆代码
cd /opt
sudo git clone https://github.com/Nami3Piece/twitter-monitor.git
cd twitter-monitor

# 安装 Python 依赖
sudo pip3 install -r requirements.txt

# 创建环境变量文件
sudo nano .env
# 粘贴你的环境变量（ANTHROPIC_API_KEY 等）

# 创建 systemd 服务
sudo tee /etc/systemd/system/twitter-monitor.service > /dev/null <<EOF
[Unit]
Description=Twitter Monitor Service
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/twitter-monitor
Environment="PATH=/usr/bin"
ExecStart=/usr/bin/python3 /opt/twitter-monitor/main.py
Restart=always

[Install]
WantedBy=multi-user.target
EOF

# 启动服务
sudo systemctl daemon-reload
sudo systemctl enable twitter-monitor
sudo systemctl start twitter-monitor

# 配置 Nginx 反向代理
sudo tee /etc/nginx/sites-available/twitter-monitor > /dev/null <<EOF
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    }
}
EOF

sudo ln -sf /etc/nginx/sites-available/twitter-monitor /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx

# 检查状态
sudo systemctl status twitter-monitor
DEPLOY_SCRIPT

echo ""
echo "3. 在 Cloudflare DNS 中："
echo "   - 将 monitor.dailyxdigest.uk 的 A 记录指向你的服务器 IP"
echo ""
echo "✅ 部署完成！访问 http://你的服务器IP 测试"
echo ""
echo "📦 代码仓库: https://github.com/Nami3Piece/twitter-monitor"
