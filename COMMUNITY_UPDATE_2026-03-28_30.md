# 社群更新 | Community Update — 2026-03-28 至 2026-03-30

---

## 中文版

亲爱的社群朋友们，

过去三天我们进行了大量深度优化，覆盖账号质量管理、内容过滤、VIP 监控和界面体验，感谢大家的持续反馈！

---

### 🆕 新功能

**1. 官方账号置顶横幅**
ARKREEN、GREENBTC、TLAY 三个项目标签页顶部新增官方账号动态横幅，展示官推账号最新推文，并附上 Profile 封面长图、粉丝数和帖子数。官方内容独立展示，不参与投票排序。

**2. VIP 账号优先监控系统**
新增 VIP 账号专项监控：被投票过或手动关注的账号，每8小时直接抓取最新推文（0:30 / 8:30 / 16:30 UTC），确保高质量账号每24小时至少被覆盖一次。同时支持：
- VIP 账号免媒体图片要求
- 回复类推文自动获取原帖内容
- 每账号每轮最多1条，避免刷屏

**3. 账号列表管理功能**
账号列表新增三项操作：
- 🔍 **实时搜索**：输入关键词即时过滤账号
- ❌ **一键删除**：直接从列表移除账号及未投票推文
- ➕ **手动添加**：输入用户名即可添加并自动设为关注

**4. 项目 Tab 官方 X 链接**
每个项目 Tab 标题旁新增 ↗ 跳转链接，直达官方 X 账号：
ARKREEN → @arkreen_network · GREENBTC → @GreenBTCClub · TLAY → @tlay_io · AI_RENAISSANCE → @claudeai

---

### 🧹 账号库深度清理

对全部四个项目（ARKREEN / GREENBTC / TLAY / AI_RENAISSANCE）进行了系统性清理：

- **删除噪音大号**：移除 @TheEconomist (25.9M)、@Reuters、@XHNews、@ChinaDaily 等无关大媒体
- **删除从未产出推文的账号**：四个项目合计清理约 **2,300+** 个从未产出任何推文的空账号
- **删除地区噪音**：移除全部印度、拉斯维加斯本地号等明显无关账号
- **删除泛匹配 KOL**：移除 @IGN、@Forbes、@Defi_Rocketeer 等泛话题 KOL
- **账号总量变化**：ARKREEN 945 → 52，GREENBTC 395 → ~267，TLAY 468 → 155，AI_RENAISSANCE 1206 → 211

清理后账号库全部为有实际内容产出或被用户投票认可的高质量账号。

---

### ⚙️ 内容过滤优化

- **取消媒体（图片）硬性要求**：之前要求推文必须带图才入库，导致 90%+ 账号从未产出内容。现已全面取消，所有通过关键词过滤的推文均可入库
- **展示窗口从 24h 扩展到 48h**：低活跃项目不再只展示 1-2 条
- **黑名单扩充**：新增 15+ 个噪音账号防止重新抓取

---

### 🐛 修复

- 账号删除/添加认证改为 Cookie 方式，不再弹出浏览器登录框
- Claude Code 社区动态默认隐藏，有真实内容时才显示
- VIP 账号回复推文现在会显示原帖内容引用块

---

每天 UTC 0:00（北京时间早八点），准时为您带来 Web3 核心信号。

🌐 https://monitor.dailyxdigest.uk · 免费订阅

---

## English Version

Dear Community,

We've shipped a major wave of improvements over the past three days — covering account quality management, content filtering, VIP monitoring, and UI experience. Thank you for all the feedback!

---

### 🆕 New Features

**1. Official Account Pinned Banner**
ARKREEN, GREENBTC, and TLAY project tabs now show a pinned banner at the top with the latest tweet from the official account, including profile banner image, follower count, and post count. Official content is displayed separately and excluded from the voting queue.

**2. VIP Account Priority Monitoring**
New dedicated VIP monitor for voted or manually followed accounts: runs every 8 hours (0:30 / 8:30 / 16:30 UTC) to fetch the latest tweet directly via `from:username` search — ensuring every high-quality account is covered at least once every 24 hours. Features:
- No media (image) requirement for VIP accounts
- Replies automatically fetch the original tweet content
- Max 1 tweet per VIP account per cycle to prevent flooding

**3. Account List Management**
Three new actions in the account list panel:
- 🔍 **Live search**: Filter accounts in real-time by keyword
- ❌ **Delete**: Remove an account and its unvoted tweets instantly
- ➕ **Add manually**: Enter a username to add and auto-follow

**4. Project Tab X Links**
Each project tab now has a ↗ link to the official X account:
ARKREEN → @arkreen_network · GREENBTC → @GreenBTCClub · TLAY → @tlay_io · AI_RENAISSANCE → @claudeai

---

### 🧹 Account Database Deep Clean

All four projects underwent systematic cleanup:

- **Removed irrelevant mega accounts**: @TheEconomist (25.9M), @Reuters, @XHNews, @ChinaDaily and other off-topic media giants
- **Removed never-active accounts**: ~2,300+ accounts that matched keywords but never produced a single tweet in the database
- **Removed regional noise**: All India-linked accounts, Las Vegas local accounts, and other clearly off-topic handles
- **Removed generic KOLs**: @IGN, @Forbes, @Defi_Rocketeer and similar broad-topic accounts
- **Account count changes**: ARKREEN 945→52 · GREENBTC 395→~267 · TLAY 468→155 · AI_RENAISSANCE 1206→211

The remaining accounts are exclusively those with actual tweet output or user-validated votes.

---

### ⚙️ Content Filter Updates

- **Removed hard media requirement**: Previously all tweets required an image/video to enter the database — causing 90%+ of tracked accounts to never produce content. This requirement is now fully removed
- **Display window extended from 24h to 48h**: Low-activity projects no longer show only 1–2 tweets
- **Blocklist expanded**: 15+ noisy accounts added to prevent re-fetching

---

### 🐛 Bug Fixes

- Account delete/add now uses cookie-based auth — no more browser login popup
- Claude Code community insight hidden by default, only shown when real content is available
- VIP account replies now display the original tweet as a quoted block

---

Every day at UTC 0:00 (8AM Beijing), we deliver your Web3 core signals.

🌐 https://monitor.dailyxdigest.uk · Free to follow
