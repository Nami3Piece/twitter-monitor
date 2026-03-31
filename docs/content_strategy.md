# Daily X Digest — 内容筛选与推荐策略

**版本**：v1.2 · **日期**：2026-03-31
**适用项目**：ARKREEN · GREENBTC · TLAY

> 本文档基于：① 2026-03-25 用户删除记录（40 条真实反馈）② X 推荐算法 2023 开源原文深度解读 ③ 2024–2026 年算法演进跟踪

---

## 一、我们的品味主张

我们不是新闻聚合器，不是交易信号机器，不是政治评论员。
我们关注的是：**真实世界资产上链 × 清洁能源 × 物理 AI 的交叉地带**——这是一个正在发生的、安静而深刻的基础设施变革。

> **一句话筛选原则：这条推文，能不能帮我们的目标用户更好地理解"现实世界如何上链"？**

---

## 二、通用硬性排除规则（三个项目均适用）

以下内容**无论关键词匹配、无论热度多高，一律不纳入**：

### 2.0 抓取频率限制

**同一账号在 24 小时内只抓取一次。**

- 同一 `username` 的推文在 DB 中已有 24h 内的记录时，跳过该账号的后续推文，不再写入。
- 适用于所有项目、所有监控账号，无例外。
- 目的：防止高频发推账号（如 @RWAwatchlist_）占据单个项目的全部版面，保持内容多样性。
- 执行层：`monitor/keyword_monitor.py` 写入前检查，`digest_generator.py` 数据准备阶段同样去重。

### 2.1 政治与地缘

| 排除类型 | 真实案例 |
|---------|---------|
| 提及国家元首（美国总统、中国领导人等） | "thank you mr president everything was green" |
| 中美、中日、俄乌、伊朗、中东冲突 | 土耳其能源部长谈中东地缘危机对全球经济的影响 |
| 气候政治（协议谈判、绿色政治党派） | 加拿大人民党领导人反对净零排放 |
| 国际制裁、石油价格战争 | 黑石 CEO 谈油价 $150 触发全球衰退 |

**原则**：我们关注技术与协议，不关注政治博弈。

### 2.2 价格与交易引导

| 排除类型 | 真实案例 |
|---------|---------|
| BTC/ETH 价格分析、K 线解读 | "BTC monthly looks solid, 1H 200SMA key level" |
| 资金费率、清算预测、做多做空建议 | "$BTC Funding stays positive + Coinbase in deep red" |
| "绿/红月份"等市场情绪引导 | "Is April a red or green month?" |
| 引用总统/政策推动市场情绪 | "everything was green for 5 minutes — now we're back to reality" |

**原则**：我们不引导交易决策，不做币价预测。

### 2.3 垃圾与低质量账号

| 排除类型 | 真实案例 |
|---------|---------|
| 病毒式互动（follow/RT/reply 换资源） | "Paid Courses FREE — Follow + RT + Reply 'All'" |
| 无意义 GM 推文 | "🚀 GM GM Crypto Fam ☀️ new gains loading 💎🙌" |
| 第三方活动宣传（非我方三个项目） | NDTV 印度可持续发展任务活动通知 |
| 账号质量差（高频发布、内容单一、粉丝 < 500） | 纯 meme 账号 / 机器人账号 |

### 2.4 关键词语义误伤

| 关键词 | 误伤类型 | 排除判断 |
|--------|---------|---------|
| `robot` | 猫砂盆机器人、高达动漫、FRC 竞赛 | 需结合 Physical AI / 工业 / 自主系统语境 |
| `robo` | 加密 meme 账号（roboPBOC）、游戏角色 | 需结合 robotaxi / DePAI 语境 |
| `climate` | 极端天气讨论、气候正义运动、农业遗产 | 需结合能源转型、碳市场、清洁技术 |
| `Green Bitcoin` | 一般 crypto 看涨看跌、GM 推文 | 需包含 sustainable / mining energy / carbon |
| `RWA Data` | 女性投资策略、传统金融理财 | 需包含 blockchain / on-chain / DeFi |
| `IoT Oracle` | 甲骨文公司（Oracle EBS）OCI 产品 | 需结合 DePIN / sensor / 链上数据 |

---

## 三、项目专属筛选策略

### 3.1 ARKREEN — 清洁能源上链基础设施

**我们想看：**
- DePIN / 分布式能源设备连接区块链
- 可再生能源证书（REC）上链、绿证数字化
- 能源 IoT 数据、智能电网与 Web3 结合
- 碳信用、碳市场、链上结算
- 实际能源设备（光伏、储能）的数字孪生

**我们不想看：**
- 传统光伏装机新闻（印度/土耳其大型电站 PPA 签约）——除非明确涉及上链/DePIN
- 极端天气、气候科学研究、农业气候
- 能源地缘政治（油价、制裁、战争对能源供应的影响）
- 气候峰会、政府气候政策辩论

**判断标准：**
> "这条推文在讨论能源的**数字化 / 上链 / 去中心化**，还是只是在讨论传统能源行业新闻？"

---

### 3.2 GREENBTC — 比特币绿色可持续

**我们想看：**
- 比特币矿业使用可再生能源的实际进展
- 比特币能耗数据、碳足迹研究
- 矿业与过剩电力消纳（水电、风电、太阳能）
- 比特币作为能源买家/储能单元的探索
- 绿色矿场建设、碳中和挖矿

**我们不想看：**
- 比特币价格分析、K 线、技术面
- 任何市场交易引导（做多做空、资金费率）
- 提及美国总统、地缘政治的 BTC 情绪内容
- "绿"字误伤（K 线绿色、绿色月份）

**判断标准：**
> "这条推文里的'绿'，是指**能源/可持续性**，还是**价格涨跌**？"

---

### 3.3 TLAY — 物理 AI · DePAI · 自主机器人

**我们想看：**
- Physical AI（机器人感知、操控、实体 AI 落地）
- DePAI / 去中心化物理 AI 基础设施
- Robotaxi、自动驾驶实际部署进展
- 工业机器人、仓储机器人、人形机器人
- IoT 传感器链上数据、DePIN 物理节点
- RWA：真实世界资产上链的技术探索

**我们不想看：**
- 动漫机器人、游戏机器人（高达、FRC 竞赛）
- 宠物自动化设备（猫砂盆机器人）
- 纯 crypto 叙事（NFT、meme、Web3 艺术）
- Tesla FSD 用户反馈帖子（非官方、非数据）
- 传统金融 RWA 内容（女性投资、股票理财）

**判断标准：**
> "这条推文的机器人/AI，是在**物理世界执行任务**，还是只是一个词语/表情符号？"

---

## 四、X 推荐算法解读与策略启示

### 4.1 算法三阶段架构（2023 开源原文）

X 每天从 **5 亿条推文**中为每用户筛出约 **150 条**，分三阶段：

```
5亿条推文
    ↓ 候选生成（Candidate Sourcing）
  ~1,500 条候选
    ↓ 神经网络排序（Ranking）
   ~500 条有序内容
    ↓ 启发式过滤（Heuristics & Filters）
   ~150 条最终展示
```

**候选生成（两路来源）**

| 来源 | 技术 | 说明 |
|------|------|------|
| 网络内（In-Network，约50%） | Real Graph 模型 | 预测你与关注者互动概率，互动越频繁权重越高 |
| 网络外（Out-of-Network，约50%） | GraphJet（社交图谱遍历）+ SimClusters（14.5万语义社区） | "你关注的人最近互动了什么"+"与你兴趣相似的人在看什么" |

**排序公式（互动权重）**

```
推文得分 = 点赞×1 + 转发×20 + 回复×13.5
         + 主页点击×12 + 链接点击×11 + 书签×10
```

> 核心洞察：**转发（×20）权重是点赞（×1）的 20 倍**。X 本质上更重视"传播"而非"认可"。5 条深度回复 > 50 个点赞。

**启发式过滤**
- 同一作者连续出现不超过 2 条
- 被标记"不感兴趣"的内容持续降权
- 网络外内容只有在关注者也互动过时才展示（社交证明）

---

### 4.2 2023 → 2026 算法演进：哪些变了，哪些没变

| 维度 | 2023 原文 | 2026 现状 | 对我们适用性 |
|------|-----------|-----------|------------|
| 三阶段漏斗架构 | 候选生成→排序→过滤 | 框架保留，实现升级 | ✅ 90% 仍适用 |
| SimClusters 语义社区 | 14.5万虚拟社区，矩阵分解 | 仍是基础，Grok 在其上增强语义理解 | ✅ 85% 仍适用 |
| 互动权重公式 | 转发×20 > 回复×13.5 > 点赞×1 | 结构保留，Grok 增加情感质量维度 | ✅ 80% 仍适用 |
| 排序引擎 | 4800万参数神经网络 | **已替换为 Grok AI（Phoenix 组件）**，每天读取 1亿+ 条内容 | ⚠️ 底层变化，信号结构保留 |
| 外链惩罚 | 未提及 | **包含外链触达降低 30~90%**（Musk 本人确认） | ❌ 2023完全缺失，2026关键信号 |
| Premium 加权 | 未提及 | **Premium 用户触达是免费的 2x~10x** | ❌ 新增，影响内容质量判断 |
| Following 流时序 | 时间线顺序 | **2025.11起 Following 流也被 Grok AI 排序**，非纯时间顺序 | ❌ 重大变化 |
| 情感语气分析 | 仅负反馈降权 | Grok 分析语气：正面/建设性加权，对抗/煽动降权 | 🆕 新信号，与我们策略方向一致 |
| 透明度 | 首次开源 | 每4周强制开源一次（2026.01起） | ✅ 持续可追踪 |

**2026年综合结论**：底层架构（候选生成→排序→过滤）仍然成立，但 Grok AI 全面接管排序层，叠加外链惩罚和 Premium 加权，2026 年的 X 算法在**表层信号**上与 2023 年有实质差异。

---

### 4.3 X 算法对我们筛选策略的直接启示

| X 算法信号 | 我们的应用策略 |
|-----------|--------------|
| **回复×13.5 权重高** | 优先收录有实质讨论（≥3条回复）的推文，而非单纯高赞 |
| **外链推文触达 -50%** | 优先无外链的原创观点推文——这类内容在 X 上是"逆势突围者"，内容质量更高 |
| **SimClusters 语义社区** | 关键词升级为语义词组，`robot` 无意义，`physical AI deployment` 有意义 |
| **Grok 情感分析** | 建设性内容加权——我们已排除市场情绪和政治内容，策略方向正确 |
| **时间衰减（6小时减半）** | 24小时内发布优先；同一话题超过48小时降权 |
| **账号 PageRank** | 被高信誉账号转发的推文优先；`poor_account` 黑名单永久屏蔽 |
| **Premium 放大失真** | 互动数量不等于真实影响力；区分 Premium 放大互动与真实有机互动 |
| **Following 流全面 AI 排序** | 我们的 Daily Digest 本质上是在复现更精准的 For You 流，2026年定位价值更高 |

---

## 五、内容质量评分机制

### 5.1 推文质量得分（综合计算）

```
质量得分 = 互动质量分(40%) + 内容相关分(35%) + 账号信誉分(25%)
```

**互动质量分**
- 回复数 ≥ 5：+30
- 转发数 ≥ 10：+20
- 无外链（原创内容）：+15
- 点赞数 ≥ 50：+10
- 发布时间 < 12h：+10，< 24h：+5

**内容相关分**
- 命中正向语义词组（见第六节）：+40
- 关键词精准匹配：+20
- 包含具体数据/数字：+15
- 含外链但来源权威（官方账号/研究机构）：+10

**账号信誉分**
- 粉丝 > 100,000：+30
- 粉丝 10,000~100,000：+20
- 粉丝 1,000~10,000：+10
- 历史删除 0 次：+10
- 历史删除 ≥1 次（非 poor_account）：+0
- poor_account 历史删除 ≥3 次：**直接屏蔽**

### 5.2 硬排除（命中即丢弃，不参与评分）

```
政治词库：US president | Russia Ukraine | Iran | Gaza | Middle East conflict
         | 中美关系 | 俄乌 | 制裁 | geopolitical

价格词库：price prediction | buy signal | sell signal | bullish | bearish
         | K线 | funding rate | 做多 | 做空 | 爆仓

垃圾词库：follow + RT | GM GM | free course | airdrop | 100x gem
         | like and retweet | drop your wallet
```

---

## 六、正向语义词组（关键词增强）

命中以下词组至少 **1 个** 才视为相关内容：

### ARKREEN
`DePIN` · `on-chain energy` · `energy IoT` · `renewable certificate` · `REC on-chain` · `green certificate` · `carbon credit blockchain` · `energy data oracle` · `smart meter web3` · `decentralized energy` · `energy NFT` · `solar DePIN`

### GREENBTC
`bitcoin mining energy` · `sustainable mining` · `renewable mining` · `carbon neutral bitcoin` · `mining solar` · `mining wind` · `mining hydro` · `bitcoin energy consumption` · `stranded energy bitcoin` · `green hashrate` · `bitcoin carbon footprint`

### TLAY
`physical AI` · `DePAI` · `autonomous robot deployment` · `humanoid robot` · `robotaxi launch` · `self-driving miles` · `robot manipulation` · `on-chain sensor data` · `DePIN hardware node` · `RWA tokenization` · `real world asset on-chain`

---

## 七、账号黑白名单机制

| 类别 | 判定条件 | 处理方式 |
|------|---------|---------|
| **白名单** | 项目官方账号、知名研究机构、行业头部 KOL | 直接纳入，评分加权 |
| **灰名单** | 历史删除 1~2 次（非 poor_account） | 正常评分，额外审核 |
| **黑名单** | poor_account 删除 ≥3 次 | 永久屏蔽，不重新插入 |
| **临时屏蔽** | 单日发垃圾内容 ≥3 条 | 屏蔽 7 天后自动解除 |

---

## 八、我们的内容品味关键词

我们不是在看新闻，我们在寻找**信号**。

✅ **偏好的内容调性**
- 数据驱动（具体数字、节点数、能耗数据、增长率）
- 技术进展（协议升级、硬件部署、集成案例）
- 行业洞察（研究报告、趋势分析、学术引用）
- 生态合作（项目间互动、合作公告、生态扩张）
- 原创观点（无外链、作者直接表达、引发讨论）

❌ **回避的内容调性**
- 情绪化市场评论（"bullish AF" "number go up"）
- 政治立场表达（任何方向）
- 无数据支撑的宏观预测
- 与三个项目无直接关联的行业噪音
- 事件驱动的一次性新闻（无持续讨论价值）

---

## 九、参考资料

- [Twitter's Recommendation Algorithm（2023 官方开源原文）](https://blog.x.com/engineering/en_us/topics/open-source/2023/twitter-recommendation-algorithm)
- [GitHub: twitter/the-algorithm](https://github.com/twitter/the-algorithm)
- [X's Algorithm Is Shifting to Grok-Powered AI（2025）](https://www.socialmediatoday.com/news/x-formerly-twitter-switching-to-fully-ai-powered-grok-algorithm/803174/)
- [X Now Algorithmically Ranks Following Feed（2025.11）](https://www.socialmediatoday.com/news/x-formerly-twitter-sorts-following-feed-algorithm-ai-grok/806617/)

---

## 十、版本记录

| 版本 | 日期 | 变更内容 |
|------|------|---------|
| v1.0 | 2026-03-25 | 基于首日 40 条删除记录初稿 |
| v1.1 | 2026-03-25 | 合并 X 算法 2023 原文解读 + 2024–2026 演进分析；增加评分机制、黑白名单、正向语义词组 |
| v1.2 | 2026-03-31 | 新增 §2.0 抓取频率限制：同一账号 24h 内只抓取一次，防止高频账号占据版面 |

---

*本文档由 Daily X Digest 项目组维护，基于真实用户删除反馈 + X 算法每月开源更新持续迭代。*
