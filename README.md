# 🐦 Twitter Monitor

一个功能强大的 Twitter 内容监控和管理系统，专为 Web3 项目设计。自动追踪关键词、管理账号、投票推文，并使用 AI 生成转发评论。

## ✨ 核心功能

### 1. 关键词监控
- 📊 多项目支持（ARKREEN、GREENBTC、TLAY、AI_RENAISSANCE）
- 🔍 自动追踪指定关键词的推文
- ⏰ 可配置的轮询间隔
- 📈 实时统计和数据分析

### 2. 账号管理
- 👥 自动发现和追踪相关账号
- 📊 粉丝数统计和排序
- ✅ 自动关注高质量账号（3票自动关注）
- 🧹 低粉丝账号清理功能

### 3. 推文投票系统
- ✓ 多用户投票支持
- 🎯 投票达标自动关注作者
- 📊 投票统计和排行
- 👤 个人投票历史追踪

### 4. AI Retweet Draft（新功能）
- 🤖 使用 Claude API 生成转发评论草稿
- 💼 **Professional**：正式、有见地、行业聚焦
- 😊 **Casual**：友好、对话式、易于理解
- 🎉 **Enthusiastic**：兴奋、充满活力、支持性
- 📋 一键复制到剪贴板
- 🔄 API 失败时手动重试
- 📊 实时字符数统计

### 5. Web Dashboard
- 📱 响应式设计，支持移动端
- 🌓 深色主题界面
- 🔍 实时搜索和过滤
- 📊 数据可视化统计
- 🎨 项目颜色标识

### 6. 用户认证系统
- 🔐 JWT Token 认证
- 📧 邮箱 OTP 验证（Resend.com）
- 🌐 Google OAuth 2.0 登录
- 🐦 X (Twitter) OAuth 2.0 登录
- 💳 Stripe 订阅支付集成

### 7. 通知系统
- 📱 Telegram Bot 通知
- 🔔 实时推文提醒
- 📊 每日统计报告

### 8. 数据管理
- 💾 SQLite 数据库
- 📤 推文分享列表
- 🗑️ 批量删除功能
- 📊 关键词统计分析

### 9. Contribution Hub
- ✨ AI 关键词建议
- 🔗 URL/内容分析
- ➕ 快速添加关键词
- 🎯 智能项目匹配

## 🚀 快速开始

### 环境要求

- Python 3.9+
- SQLite 3
- 网络连接

### 安装步骤

1. **克隆仓库**
```bash
git clone https://github.com/Nami3Piece/twitter-monitor.git
cd twitter-monitor
```

2. **安装依赖**
```bash
pip install -r requirements.txt
```

3. **配置环境变量**

创建 `.env` 文件：

```bash
# Twitter API
TWITTERAPI_KEY=your_twitter_api_key

# 项目关键词
ARKREEN_KEYWORDS=DePIN Energy,Solar Energy,Renewable Energy,...
GREENBTC_KEYWORDS=Bitcoin Energy,Green Bitcoin,...
TLAY_KEYWORDS=Machine Economy,IoT Oracle,...
AI_RENAISSANCE_KEYWORDS=AI Agent,Claude Code,...

# 轮询间隔（秒）
POLL_INTERVAL=300

# Telegram 通知
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# Web Dashboard 认证
WEB_USER=admin
WEB_PASSWORD=your_password

# Claude AI（AI Retweet Draft 功能）
ANTHROPIC_API_KEY=sk-ant-xxx
ANTHROPIC_BASE_URL=https://code.newcli.com/claude/aws  # 可选
AI_ENABLED=1

# JWT 密钥
JWT_SECRET=your_jwt_secret

# 邮箱 OTP（Resend.com）
RESEND_API_KEY=your_resend_key
RESEND_FROM=noreply@yourdomain.com

# Google OAuth 2.0
GOOGLE_CLIENT_ID=your_client_id
GOOGLE_CLIENT_SECRET=your_client_secret
GOOGLE_REDIRECT_URI=https://yourdomain.com/auth/google/callback

# X (Twitter) OAuth 2.0
X_CLIENT_ID=your_client_id
X_CLIENT_SECRET=your_client_secret
X_REDIRECT_URI=https://yourdomain.com/auth/x/callback

# Stripe 支付
STRIPE_SECRET_KEY=your_stripe_key
STRIPE_PUBLISHABLE_KEY=your_publishable_key
STRIPE_WEBHOOK_SECRET=your_webhook_secret
```

4. **初始化数据库**
```bash
python3 -c "from db.database import init_db; import asyncio; asyncio.run(init_db())"
```

5. **启动服务**
```bash
# 启动监控服务
python3 main.py

# 启动 Web Dashboard（另一个终端）
python3 web.py
```

6. **访问 Dashboard**
```
http://localhost:8000
```

## 📖 使用指南

### AI Retweet Draft 使用方法

1. 在推文列表中找到感兴趣的推文
2. 点击推文卡片上的 **"✨ AI Draft"** 按钮
3. 等待 AI 生成3种风格的草稿（约2-3秒）
4. 切换标签页查看不同风格：
   - 💼 Professional
   - 😊 Casual
   - 🎉 Enthusiastic
5. 点击 **"📋 Copy to Clipboard"** 复制草稿
6. 粘贴到 Twitter 进行转发

**注意**：如果 Claude API 不可用，会显示错误信息和 "🔄 Retry" 按钮，等待 API 恢复后手动重试。

### 投票功能

1. 浏览推文列表
2. 点击 **"✓ Vote"** 按钮为推文投票
3. 达到3票自动关注作者
4. 在 **"✓ Voted"** 标签查看已投票推文

### 关键词管理

1. 访问 `/admin/keywords` 页面
2. 查看各项目的关键词列表
3. 添加或删除关键词
4. 使用 **Contribution Hub** 的 AI 建议功能

### 账号管理

1. 在 **"Accounts"** 标签查看追踪的账号
2. 查看粉丝数和投票统计
3. 使用清理功能移除低质量账号

## 🏗️ 项目结构

```
twitter-monitor/
├── ai/
│   ├── __init__.py
│   ├── retweet.py              # 模板转发生成
│   ├── claude_retweet.py       # Claude AI 转发生成
│   └── engagement.py           # AI 互动建议
├── api/
│   ├── __init__.py
│   └── twitterapi.py           # Twitter API 封装
├── db/
│   ├── __init__.py
│   └── database.py             # 数据库操作
├── monitor/
│   ├── __init__.py
│   └── keyword_monitor.py      # 关键词监控核心
├── notifiers/
│   ├── __init__.py
│   ├── console.py              # 控制台通知
│   └── telegram.py             # Telegram 通知
├── agent/
│   └── daily_voter.py          # 自动投票代理
├── auth.py                     # 用户认证
├── config.py                   # 配置管理
├── web.py                      # Web Dashboard
├── main.py                     # 主程序入口
├── requirements.txt            # Python 依赖
├── .env.example                # 环境变量示例
├── .gitignore                  # Git 忽略规则
└── README.md                   # 本文件
```

## 🧪 测试

### 测试 AI Retweet Draft

```bash
python3 test_ai_draft.py
```

### 测试数据库连接

```bash
python3 -c "from db.database import init_db; import asyncio; asyncio.run(init_db())"
```

## 🔧 配置说明

### 关键词配置

在 `.env` 文件中为每个项目配置关键词：

```bash
ARKREEN_KEYWORDS=DePIN Energy,Solar Energy,Renewable Energy
GREENBTC_KEYWORDS=Bitcoin Energy,Green Bitcoin
TLAY_KEYWORDS=Machine Economy,IoT Oracle
AI_RENAISSANCE_KEYWORDS=AI Agent,Claude Code
```

### AI 功能配置

```bash
# 启用/禁用 AI 功能
AI_ENABLED=1

# Claude API 配置
ANTHROPIC_API_KEY=sk-ant-xxx
ANTHROPIC_BASE_URL=https://code.newcli.com/claude/aws  # 可选
```

### 通知配置

```bash
# Telegram Bot
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

## 📊 数据库结构

### tweets 表
- tweet_id, project, keyword, username
- text, url, created_at
- like_count, retweet_count, reply_count, view_count
- media_url, is_reply, reply_to_text
- ai_reply, ai_quotes, ai_comments

### accounts 表
- username, project, keywords
- followers_count, followed, vote_count

### votes 表
- tweet_id, user_id, voted_at

### users 表
- id, email, nickname, auth_provider
- subscription_tier, subscription_status

## 🚀 部署

### Render.com 部署

项目已配置 `render.yaml`，可直接部署到 Render.com：

1. 连接 GitHub 仓库
2. 配置环境变量
3. 自动部署

### 其他平台

支持部署到：
- Heroku
- Railway
- Fly.io
- 阿里云
- PythonAnywhere

详见各平台的部署指南文件。

## 🔐 安全性

- ✅ 所有敏感信息通过环境变量配置
- ✅ JWT Token 认证
- ✅ HTTP Basic Auth 保护管理端点
- ✅ SQL 注入防护
- ✅ XSS 防护
- ✅ CSRF 防护
- ✅ 安全响应头

## 📝 更新日志

### v1.3.0 (2026-03-16)
- ✨ 新增 AI Retweet Draft 功能
- 🤖 集成 Claude API
- 💼 3种风格草稿生成
- 🔄 手动重试机制
- 📋 一键复制功能

### v1.2.0
- ✨ 添加 AI 互动建议（Quote + Comment）
- 🎯 Contribution Hub
- 📊 关键词统计分析

### v1.1.0
- 🔐 用户认证系统
- 💳 Stripe 订阅集成
- 📧 邮箱 OTP 验证

### v1.0.0
- 🎉 初始版本发布
- 📊 关键词监控
- 👥 账号管理
- ✓ 投票系统

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📄 许可证

MIT License

## 👥 作者

- **NamiPieces** - [GitHub](https://github.com/Nami3Piece)

## 🙏 致谢

- [Anthropic Claude](https://www.anthropic.com/) - AI 功能支持
- [FastAPI](https://fastapi.tiangolo.com/) - Web 框架
- [Telegram Bot API](https://core.telegram.org/bots/api) - 通知系统

## 📞 联系方式

- GitHub Issues: https://github.com/Nami3Piece/twitter-monitor/issues
- Email: 通过 GitHub 联系

---

**⭐ 如果这个项目对您有帮助，请给个 Star！**
