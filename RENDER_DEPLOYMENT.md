# Twitter Monitor 部署到 Render.com 指南

## 准备工作

已创建的文件：
- `.gitignore` - Git 忽略文件
- `render.yaml` - Render 配置文件
- `Procfile` - 进程启动文件
- `runtime.txt` - Python 版本
- `requirements.txt` - 依赖包（已存在）

代码已修改：
- `main.py` - 支持 PORT 环境变量，监听 0.0.0.0

## 部署步骤

### 1. 创建 GitHub 仓库

```bash
cd ~/twitter-monitor
git add .
git commit -m "Initial commit for Render deployment"
```

在 GitHub 创建新仓库，然后：
```bash
git remote add origin https://github.com/你的用户名/twitter-monitor.git
git branch -M main
git push -u origin main
```

### 2. 在 Render.com 部署

1. 访问 https://render.com 并注册/登录
2. 点击 "New +" → "Web Service"
3. 连接你的 GitHub 仓库
4. 选择 `twitter-monitor` 仓库
5. Render 会自动检测到 `render.yaml` 配置

### 3. 配置环境变量

在 Render 控制台添加以下环境变量：

**必需的环境变量：**
- `WEB_USER` - 网站登录用户名
- `WEB_PASSWORD` - 网站登录密码
- `ANTHROPIC_API_KEY` - Claude API key
- `TWITTER_BEARER_TOKEN` - Twitter Bearer Token
- `TWITTER_API_KEY` - Twitter API Key
- `TWITTER_API_SECRET` - Twitter API Secret
- `TWITTER_ACCESS_TOKEN` - Twitter Access Token
- `TWITTER_ACCESS_SECRET` - Twitter Access Secret

**可选的环境变量：**
- `TELEGRAM_BOT_TOKEN` - Telegram 机器人 token
- `TELEGRAM_CHAT_ID` - Telegram 聊天 ID

### 4. 部署

点击 "Create Web Service"，Render 会自动：
1. 克隆你的仓库
2. 安装依赖 (`pip install -r requirements.txt`)
3. 启动服务 (`python3 main.py`)

### 5. 获取 URL

部署完成后，Render 会提供一个免费的 URL：
```
https://twitter-monitor-xxxx.onrender.com
```

## 免费套餐限制

- 15 分钟无活动后会休眠
- 每月 750 小时免费运行时间
- 重启后需要几秒钟唤醒

## 保持服务在线

可以使用 UptimeRobot 或 Cron-job.org 每 10 分钟 ping 一次你的服务：
```
https://你的服务.onrender.com/api/tweets
```

## 本地测试

在推送到 GitHub 前，本地测试：
```bash
export PORT=8080
python3 main.py
```

访问 http://localhost:8080 确认正常运行。

## 注意事项

1. 不要将 `.env` 文件提交到 Git
2. 所有敏感信息通过 Render 环境变量配置
3. 数据库文件会在每次重启后重置（免费套餐无持久化存储）
4. 如需持久化，考虑使用 Render 的 PostgreSQL 服务（免费 90 天）
