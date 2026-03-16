# Render 服务保活指南

## 问题
Render 免费套餐会在 15 分钟无活动后自动休眠。

## 解决方案

### 方案 1: 使用 Cron-job.org（推荐，完全免费）

1. **注册 Cron-job.org**
   - 访问: https://console.cron-job.org/
   - 免费注册账号

2. **创建定时任务**
   - 点击 "Create cronjob"
   - Title: `Twitter Monitor Keepalive`
   - URL: `https://你的服务名.onrender.com/api/tweets`
   - Schedule: 每 14 分钟执行一次
     - 选择 "Every X minutes"
     - 输入 `14`
   - 点击 "Create cronjob"

3. **验证**
   - 任务会每 14 分钟自动 ping 你的服务
   - 服务将保持在线，不会休眠

### 方案 2: 使用 UptimeRobot（免费）

1. **注册 UptimeRobot**
   - 访问: https://uptimerobot.com/
   - 免费注册账号

2. **添加监控**
   - 点击 "+ Add New Monitor"
   - Monitor Type: `HTTP(s)`
   - Friendly Name: `Twitter Monitor`
   - URL: `https://你的服务名.onrender.com/api/tweets`
   - Monitoring Interval: `5 minutes`（免费套餐最短间隔）
   - 点击 "Create Monitor"

3. **效果**
   - 每 5 分钟检查一次服务状态
   - 同时保持服务在线
   - 额外获得服务监控和宕机通知

### 方案 3: 本地 Cron 任务

如果你有一台始终在线的电脑/服务器：

```bash
# 编辑 crontab
crontab -e

# 添加以下行（每 14 分钟执行一次）
*/14 * * * * curl -s https://你的服务名.onrender.com/api/tweets > /dev/null 2>&1
```

或使用提供的 Python 脚本：

```bash
# 编辑 crontab
crontab -e

# 添加以下行
*/14 * * * * cd ~/twitter-monitor && python3 keepalive.py >> keepalive.log 2>&1
```

### 方案 4: 升级到付费套餐（$7/月）

如果需要 24/7 在线且无需外部 ping：

1. 在 Render 控制台升级到 **Starter** 套餐
2. 价格: $7/月
3. 优势:
   - 永不休眠
   - 更快的启动速度
   - 更多资源

## 推荐配置

**最佳方案**: Cron-job.org + UptimeRobot

- **Cron-job.org**: 每 14 分钟 ping 保持在线
- **UptimeRobot**: 每 5 分钟监控服务状态，宕机时发送通知

这样既保持服务在线，又能及时发现问题。

## 验证服务状态

访问你的服务 URL，检查是否正常响应：
```bash
curl https://你的服务名.onrender.com/api/tweets
```

## 注意事项

1. **免费套餐限制**: 每月 750 小时（约 31 天）
2. **冷启动时间**: 休眠后首次访问需要 30-60 秒唤醒
3. **数据持久化**: 免费套餐重启后数据会丢失，考虑使用外部数据库

## 参考资料

- [Render Free Tier 文档](https://render.com/docs/free)
- [Cron-job.org](https://console.cron-job.org/)
- [UptimeRobot](https://uptimerobot.com/)
- [Stack Overflow: Prevent Render Server from Sleeping](https://stackoverflow.com/questions/75340700/prevent-render-server-from-sleeping)
