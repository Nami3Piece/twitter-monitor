# 用户数据库 - Twitter Monitor

**最后更新**: 2026-03-17 19:56

## 📊 注册用户列表

### 1. Nami Pieces (Owner)
- **用户ID**: `google:117585537278830150604`
- **邮箱**: nami3piece@gmail.com
- **昵称**: Nami Pieces
- **套餐**: 💎 PRO
- **状态**: Active
- **到期时间**: 2027-03-17
- **权限**:
  - 无限关键词监控
  - AI 草稿生成
  - API 访问
  - 所有高级功能

## 🔄 数据库备份

### 本地备份位置
- **路径**: `/Users/namipieces/twitter-monitor/backups/`
- **最新备份**: `tweets_20260317_195600.db`
- **备份频率**: 每次重要操作后手动备份

### 服务器数据库
- **路径**: `/var/www/twitter-monitor/data/tweets.db`
- **包含表**:
  - `users` - 用户信息
  - `subscriptions` - 订阅信息
  - `tweets` - 推文数据
  - `user_votes` - 投票记录
  - `accounts` - Twitter 账号
  - `api_keys` - API 密钥
  - `shared_lists` - 共享列表

## ⚠️ 重要事项

### 数据保护规则
1. **订阅数据**: 永久保留，不可自动删除
2. **投票数据**: 永久保留，只能手动删除
3. **用户信息**: 永久保留
4. **推文数据**: 已投票的永久保留，未投票的 24 小时后清理

### 备份策略
- 每次修改用户订阅后立即备份
- 每天自动备份一次（待实现）
- 重要操作前手动备份

## 🔧 恢复订阅的命令

如果订阅数据再次丢失，使用以下命令恢复：

```bash
ssh -i ~/.ssh/id_aliyun admin@43.103.0.20 "cd /var/www/twitter-monitor && sudo /var/www/twitter-monitor/venv/bin/python3 << 'EOF'
import asyncio
import aiosqlite
from datetime import datetime, timedelta, timezone

async def restore():
    async with aiosqlite.connect('data/tweets.db') as db:
        expires_at = (datetime.now(timezone.utc) + timedelta(days=365)).isoformat()
        await db.execute('''
            INSERT OR REPLACE INTO subscriptions (user_id, tier, status, expires_at)
            VALUES (?, ?, ?, ?)
        ''', ('google:117585537278830150604', 'pro', 'active', expires_at))
        await db.commit()
        print('✅ PRO subscription restored')

asyncio.run(restore())
EOF
"
```

## 📝 数据库检查命令

```bash
# 检查用户
ssh -i ~/.ssh/id_aliyun admin@43.103.0.20 "cd /var/www/twitter-monitor && sudo sqlite3 data/tweets.db 'SELECT * FROM users;'"

# 检查订阅
ssh -i ~/.ssh/id_aliyun admin@43.103.0.20 "cd /var/www/twitter-monitor && sudo sqlite3 data/tweets.db 'SELECT * FROM subscriptions;'"

# 检查投票数据
ssh -i ~/.ssh/id_aliyun admin@43.103.0.20 "cd /var/www/twitter-monitor && sudo sqlite3 data/tweets.db 'SELECT COUNT(*) FROM tweets WHERE voted=1;'"
```

---

**维护人员**: Claude Code
**紧急联系**: 如发现数据丢失，立即检查此文件并执行恢复命令
