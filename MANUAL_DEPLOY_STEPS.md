# 手动部署步骤

## 前提条件
- 服务器 IP: <SERVER_IP>
- 域名: monitor.dailyxdigest.uk
- 需要 root 权限

---

## 步骤 1: 登录服务器

在 Mac Terminal 中执行：
```bash
ssh root@<SERVER_IP>
```

---

## 步骤 2: 下载并执行整合部署脚本

```bash
# 下载部署脚本
wget https://raw.githubusercontent.com/Nami3Piece/twitter-monitor/main/deploy_integrated.sh

# 添加执行权限
chmod +x deploy_integrated.sh

# 执行部署
./deploy_integrated.sh
```

这个脚本会：
- 安装所有依赖（Python, Docker, Nginx, Supervisor）
- 部署 Twitter Monitor 到 /var/www/twitter-monitor
- 部署 Logo Agent 到 /var/www/logo-agent
- 配置 Nginx 反向代理（/ → Twitter Monitor, /logo/ → Logo Agent）
- 配置 SSL 证书

---

## 步骤 3: 上传配置文件

**在本地 Mac 的另一个 Terminal 窗口**执行：

```bash
# 上传 Twitter Monitor 的 .env
scp /Users/namipieces/twitter-monitor/.env root@<SERVER_IP>:/var/www/twitter-monitor/.env

# 如果 Logo Agent 有 .env，也上传
# scp /path/to/logo-agent/.env root@<SERVER_IP>:/var/www/logo-agent/.env
```

---

## 步骤 4: 重启服务

在服务器上执行：
```bash
# 重启 Twitter Monitor
supervisorctl restart all

# 重启 Logo Agent
cd /var/www/logo-agent && docker-compose restart
```

---

## 步骤 5: 配置长期在线保障

```bash
# 下载并执行长期在线配置脚本
wget https://raw.githubusercontent.com/Nami3Piece/twitter-monitor/main/ensure_uptime.sh
chmod +x ensure_uptime.sh
./ensure_uptime.sh
```

这个脚本会：
- 配置 Supervisor 和 Docker 开机自启
- 为所有服务添加自动重启策略
- 创建监控脚本（每5分钟检查服务状态）

---

## 步骤 6: 测试部署

```bash
# 下载测试脚本
wget https://raw.githubusercontent.com/Nami3Piece/twitter-monitor/main/test_deployment.sh
chmod +x test_deployment.sh
./test_deployment.sh
```

---

## 步骤 7: 验证访问

在浏览器中访问：
- **主站**: https://monitor.dailyxdigest.uk/
- **Logo Agent**: https://monitor.dailyxdigest.uk/logo/

---

## 故障排查

### 如果 Twitter Monitor 无法访问

```bash
# 检查服务状态
supervisorctl status

# 查看错误日志
tail -50 /var/log/twitter-monitor-web.err.log

# 手动重启
supervisorctl restart all
```

### 如果 Logo Agent 无法访问

```bash
cd /var/www/logo-agent

# 检查容器状态
docker-compose ps

# 查看日志
docker-compose logs backend

# 重启
docker-compose restart
```

### 如果域名无法访问

```bash
# 检查 Nginx 配置
nginx -t

# 检查 SSL 证书
certbot certificates

# 重启 Nginx
systemctl restart nginx
```

---

## 后续更新流程

当 GitHub 有新代码时，在服务器上执行：

```bash
# 更新 Twitter Monitor
cd /var/www/twitter-monitor
git pull origin main
source venv/bin/activate
pip install -r requirements.txt
supervisorctl restart all

# 更新 Logo Agent
cd /var/www/logo-agent
git pull origin main
docker-compose down
docker-compose up -d --build
```

---

## 服务管理命令

### Twitter Monitor
```bash
# 查看状态
supervisorctl status

# 重启服务
supervisorctl restart twitter-monitor-web
supervisorctl restart twitter-monitor-main

# 查看日志
tail -f /var/log/twitter-monitor-web.out.log
tail -f /var/log/twitter-monitor-main.out.log
```

### Logo Agent
```bash
cd /var/www/logo-agent

# 查看状态
docker-compose ps

# 重启服务
docker-compose restart

# 查看日志
docker-compose logs -f
```

### Nginx
```bash
# 测试配置
nginx -t

# 重启
systemctl restart nginx

# 查看日志
tail -f /var/log/nginx/error.log
```
