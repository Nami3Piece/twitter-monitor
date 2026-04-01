# RFC: Credits 积分系统 & Task Marketplace

> **Status**: Draft — 待评审
> **Author**: Nami + Claude
> **Date**: 2026-04-01
> **Related**: [twitter-monitor](https://github.com/Nami3Piece/twitter-monitor), [seo-keyword-tracker](https://github.com/Nami3Piece/seo-keyword-tracker)

## 1. 背景

当前订阅模式存在的问题：
- Pro 月费 3000 AKRE 门槛过高，低频用户不愿订阅
- Free 用户几乎无法体验核心功能，转化率低
- 缺少对社区贡献的激励机制
- 随着平台扩展到 7 个工具（SEO、Blog、Podcast、Poster 等），单一订阅难以覆盖

## 2. 方案概述

**用按次计费的 Credits 积分制替代纯月费订阅**。用户通过充值 AKRE 或完成社区任务获取积分，使用工具时按次消耗。同时支持人类用户和 Agent（API）用户。

## 3. 积分获取

### 3.1 充值

| 方式 | Credits | 说明 |
|------|---------|------|
| AKRE 充值 | 1 AKRE = 10 Credits | 链上支付，复用现有 Polygon 支付体系 |
| 新用户注册 | +100 Credits（一次性） | 让新用户能立刻体验核心功能 |

### 3.2 社区贡献

| 任务 | Credits | 频率限制 | 验证方式 |
|------|---------|---------|---------|
| 每日签到 | +5 | 1次/天 | 自动 |
| 投票筛选推文 | +2 | 50次/天 | 自动 |
| 用 AI 工具发推（带 #DailyXDigest 标签） | +15 | 3次/天 | 验证推文 URL 存在性 |
| 分享 Digest 链接到推文 | +10 | 1次/天 | 验证推文 URL 存在性 |
| 邀请新用户注册 | +50 | 10次/天 | 被邀请人完成注册 |
| 提交有效关键词 | +20 | 5次/天 | 人工审核 |
| 提交项目研究报告 | +50 | 2次/周 | 人工审核 |

### 3.3 Agent/算力任务

| 任务 | Credits | 说明 |
|------|---------|------|
| 推文情感分类 | +1/条 | 交叉验证（3人一致通过） |
| 垃圾推文识别 | +1/条 | 交叉验证 |
| Digest 翻译校对 | +5/篇 | AI + 人工审核 |
| 关键词扩展发现 | +3/个 | 系统验证有效性 |
| 竞品数据采集 | +10/份 | 格式校验 |
| AI 博客草稿生成 | +5/篇 | 质量分 > 阈值 |
| PageSpeed 分布式监控 | +2/次 | 数据格式校验 |

## 4. 积分消耗

### 4.1 Twitter Monitor

| 功能 | 消耗 | 备注 |
|------|------|------|
| 浏览推文 | 0 | 始终免费 |
| 投票 | 0 | 免费且奖励 +2 |
| AI 转推草稿 | 5 | 生成 3 种风格 |
| AI 回复草稿 | 5 | 生成 3 种风格 |
| AI 引用/评论 | 3 | 生成 3 种版本 |
| 添加自定义关键词 | 10 | 每个关键词 |
| 删除关键词 | 0 | |
| 个人过滤器 | 0 | 开通后免费 |
| Digest 下载（今天） | 0 | 始终免费 |
| Digest 下载（历史） | 5 | 每天 |
| 视频生成 | 15 | 每次 |
| 合同生成 | 20 | 每次 |

### 4.2 SEO Tracker

| 功能 | 消耗 | 备注 |
|------|------|------|
| 仪表盘浏览 | 0 | 始终免费 |
| AI 周报（最新） | 0 | 始终免费 |
| AI 周报（历史） | 10 | 每篇 |
| 自定义关键词追踪（前 3 个） | 0 | 免费额度 |
| 自定义关键词追踪（额外） | 5 | 每个/月 |
| 竞品分析 | 20 | 每次 |

### 4.3 内容工具

| 功能 | 消耗 |
|------|------|
| Blog 生成 | 15/篇 |
| Podcast 生成 | 20/集 |
| 海报设计 | 10/张 |
| 多平台格式转换 | 5/次 |

## 5. VIP 套餐（可选）

保留订阅选项给高频用户，与按次计费并行：

| 套餐 | 价格 | 包含 | 额外 |
|------|------|------|------|
| Free | $0 | 100 注册积分 + 签到 | — |
| Starter | 50 AKRE/月 | 500 Credits/月 | 积分不过期 |
| Power | 200 AKRE/月 | 2500 Credits/月 | 积分不过期 + API 优先 |
| Unlimited | 1000 AKRE/月 | 无限 | 全功能无限用 |

套餐积分每月发放，未用完不累积到下月（防通胀）。充值积分永不过期。

## 6. X 平台安全策略

### 6.1 红线（绝不做）

| 行为 | 为什么不做 |
|------|-----------|
| 付费点赞/转推指定帖子 | X 明确禁止协调性不真实行为，会导致主账号被封 |
| Bot 自动评论/点赞 | 违反 X Automation Rules |
| 短时间集中互动冲量 | 触发速率异常检测 |
| 多账号从同一 IP 操作 | 关联封禁 |
| 提供模板化评论 | 垃圾内容检测 |

### 6.2 安全边界

**我们激励的**：用户在平台上创造价值，然后**自愿**分享到 X
**我们不激励的**：对我们特定帖子的点赞/评论/转推

具体规则：
- 激励"用我们的工具写了一条推文并发布" ✅（用户自主行为）
- 激励"发布包含 #DailyXDigest 标签的原创推文" ✅（用户自主内容）
- 激励"分享 Digest 链接" ✅（内容分享，X 鼓励的行为）
- 激励"去点赞这条推文" ❌（协调性操纵）
- 激励"去评论这条推文" ❌（协调性操纵）

### 6.3 验证规则

推文类任务只验证：
1. 推文 URL 真实存在（调用 Twitter API 检查）
2. 包含指定标签（#DailyXDigest）
3. 内容非复制粘贴（与其他提交的相似度 < 70%）
4. 每人每日上限（防刷）

**不验证**互动数据（点赞数、转推数），不以互动量为奖励依据。

## 7. Agent API 设计

### 7.1 认证

```
POST /api/agent/keys/create
→ {"api_key": "dxd_agent_xxx", "rate_limit": 100}

# 所有 Agent 请求带 Header
Authorization: Bearer dxd_agent_xxx
```

### 7.2 任务流程

```
# 1. 查询可用任务
GET /api/tasks/available?type=label

# 2. 领取任务
POST /api/tasks/claim
{"task_id": "label_001"}

# 3. 提交结果
POST /api/tasks/submit
{"task_id": "label_001", "proof": {"tweet_id": "xxx", "sentiment": "positive"}}

# 4. 查看余额
GET /api/credits/balance
```

### 7.3 速率限制

| 用户类型 | 请求限制 | 任务上限 |
|---------|---------|---------|
| 人类用户 | 60次/分钟 | 按任务定义 |
| Agent (Free) | 100次/小时 | 50 tasks/天 |
| Agent (Paid) | 1000次/小时 | 500 tasks/天 |

## 8. 防刷 & 风控

| 维度 | 措施 |
|------|------|
| 注册防刷 | 新账号 24h 冷却期，仅签到可用 |
| 任务防刷 | 每人每日上限 + 全局每日发放上限 |
| 标注质量 | 交叉验证（3 人标注取多数，偏离者不得分） |
| 推文验证 | 只验证存在性，不要求互动 |
| Agent 限流 | API Key 绑定速率 + 每日任务上限 |
| 积分通胀 | 每日全局发放上限（如 10,000 Credits/天） |
| 提现门槛 | Credits → AKRE 提现需 500+ 积分 + 注册 > 7 天 |
| 异常检测 | 同 IP 多账号、短时间大量提交自动标记 |

## 9. 数据库设计

```sql
-- 用户积分余额
CREATE TABLE user_credits (
    user_id     TEXT PRIMARY KEY,
    balance     INTEGER DEFAULT 0,
    total_earned INTEGER DEFAULT 0,
    total_spent  INTEGER DEFAULT 0,
    updated_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

-- 积分交易流水
CREATE TABLE credit_transactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    amount      INTEGER NOT NULL,          -- 正数=收入，负数=消耗
    type        TEXT NOT NULL,             -- 'topup' | 'consume' | 'earn' | 'checkin' | 'referral' | 'withdraw'
    feature     TEXT,                      -- 消耗时记录功能名，如 'ai_retweet'
    description TEXT,
    balance_after INTEGER NOT NULL,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_ct_user ON credit_transactions(user_id, created_at);

-- 积分定价表
CREATE TABLE credit_prices (
    feature_id  TEXT PRIMARY KEY,          -- 如 'ai_retweet', 'blog_generate'
    credits     INTEGER NOT NULL,
    description TEXT
);

-- 任务定义
CREATE TABLE tasks (
    id          TEXT PRIMARY KEY,
    type        TEXT NOT NULL,             -- 'label' | 'translate' | 'content' | 'share' | 'keyword'
    title       TEXT NOT NULL,
    description TEXT,
    reward      INTEGER NOT NULL,          -- Credits
    max_claims  INTEGER DEFAULT 100,
    per_user    INTEGER DEFAULT 1,
    daily_limit INTEGER DEFAULT 10,
    status      TEXT DEFAULT 'active',     -- 'active' | 'paused' | 'completed'
    data        TEXT,                      -- JSON: 任务附加数据
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    expires_at  TEXT
);

-- 任务提交
CREATE TABLE task_submissions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    proof       TEXT,                      -- 推文URL / 标注JSON / 内容文本
    status      TEXT DEFAULT 'pending',    -- 'pending' | 'approved' | 'rejected'
    credits     INTEGER DEFAULT 0,
    submitted_at TEXT DEFAULT CURRENT_TIMESTAMP,
    verified_at  TEXT,
    UNIQUE(task_id, user_id, submitted_at)
);

-- Agent API Keys
CREATE TABLE agent_keys (
    api_key     TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    name        TEXT,
    rate_limit  INTEGER DEFAULT 100,
    daily_task_limit INTEGER DEFAULT 50,
    status      TEXT DEFAULT 'active',
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    last_used   TEXT
);
```

## 10. 实施路线

### Phase 1: Credits 核心（1-2 周）
- [ ] user_credits + credit_transactions 表
- [ ] 充值接口（AKRE → Credits）
- [ ] 消费接口（工具使用扣费）
- [ ] 余额查询 + 消费记录页面
- [ ] 替换现有 tier 检查为 credits 检查
- [ ] 新用户注册送 100 积分
- [ ] 每日签到

### Phase 2: 任务系统（2-3 周）
- [ ] tasks + task_submissions 表
- [ ] 任务发布 / 领取 / 提交 / 验证流程
- [ ] 推文验证（检查 URL + 标签）
- [ ] 标注任务交叉验证
- [ ] 管理后台：任务管理 + 审核

### Phase 3: Agent API（1-2 周）
- [ ] Agent API Key 管理
- [ ] 任务 API（领取/提交/余额）
- [ ] 速率限制
- [ ] Agent 文档

### Phase 4: 跨工具积分（持续）
- [ ] SEO Tracker 接入积分
- [ ] Blog Generator 接入积分
- [ ] Podcast Studio 接入积分
- [ ] Poster Designer 接入积分

## 11. 开放问题

以下问题需要评审时讨论：

1. **现有 Basic/Pro 用户迁移**：已付费用户如何过渡？建议按剩余天数折算为 Credits
2. **Credits ↔ AKRE 提现比例**：是否与充值同比（10 Credits = 1 AKRE），还是设置损耗（如 12 Credits = 1 AKRE）
3. **全局通胀控制**：每日发放上限具体数值？需要根据用户量动态调整
4. **Agent 任务质量**：标注任务交叉验证的最低人数？2 人还是 3 人
5. **VIP 套餐是否保留**：还是完全转为按次计费
6. **积分跨工具结算**：各工具独立部署时如何同步积分（共享 DB 还是 API 调用）

---

> 本文档用于发起内部评审，请在 GitHub Issue 或 Discord 讨论频道提出反馈。
