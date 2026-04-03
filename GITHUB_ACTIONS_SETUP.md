# GitHub Actions 自动部署配置指南

## 概述
配置完成后，每次 push 到 main 分支都会自动部署到阿里云服务器。

---

## 步骤 1: 在阿里云服务器上生成 SSH 密钥

登录服务器：
```bash
ssh root@<SERVER_IP>
```

生成 SSH 密钥：
```bash
ssh-keygen -t ed25519 -C "github-actions" -f ~/.ssh/github_actions_key
```

添加公钥到授权列表：
```bash
cat ~/.ssh/github_actions_key.pub >> ~/.ssh/authorized_keys
```

查看私钥（稍后需要复制到 GitHub）：
```bash
cat ~/.ssh/github_actions_key
```

**重要**: 复制完整的私钥内容，包括 `-----BEGIN OPENSSH PRIVATE KEY-----` 和 `-----END OPENSSH PRIVATE KEY-----`

---

## 步骤 2: 在服务器上初始化项目

确保项目目录存在并已部署：
```bash
# 如果还没有部署，执行整合部署
cd /root
wget https://raw.githubusercontent.com/Nami3Piece/twitter-monitor/main/deploy_integrated.sh
chmod +x deploy_integrated.sh
./deploy_integrated.sh

# 上传 .env 文件（在本地 Mac 执行）
scp /Users/namipieces/twitter-monitor/.env root@<SERVER_IP>:/var/www/twitter-monitor/.env
```

---

## 步骤 3: 在 GitHub 配置 Secrets

访问：https://github.com/Nami3Piece/twitter-monitor/settings/secrets/actions

点击 "New repository secret"，添加以下三个 secrets：

### 1. SERVER_HOST
- Name: `SERVER_HOST`
- Value: `<SERVER_IP>`

### 2. SERVER_USER
- Name: `SERVER_USER`
- Value: `root`

### 3. SERVER_SSH_KEY
- Name: `SERVER_SSH_KEY`
- Value: 步骤 1 中复制的完整私钥内容

---

## 步骤 4: 测试自动部署

配置完成后，有两种方式触发部署：

### 方式 1: 自动触发（推荐）
每次 push 到 main 分支时自动部署：
```bash
git add .
git commit -m "Update code"
git push origin main
```

### 方式 2: 手动触发
访问：https://github.com/Nami3Piece/twitter-monitor/actions

选择 "Deploy to Aliyun Server" workflow，点击 "Run workflow"

---

## 步骤 5: 查看部署状态

### 在 GitHub 查看
访问：https://github.com/Nami3Piece/twitter-monitor/actions

查看最新的 workflow 运行状态和日志。

### 在服务器查看
```bash
# 查看服务状态
supervisorctl status

# 查看日志
tail -f /var/log/twitter-monitor-web.out.log
tail -f /var/log/twitter-monitor-main.out.log
```

---

## 部署流程说明

GitHub Actions 会自动执行以下步骤：

1. ✅ 连接到阿里云服务器
2. ✅ 进入项目目录 `/var/www/twitter-monitor`
3. ✅ 拉取最新代码 `git pull origin main`
4. ✅ 激活虚拟环境
5. ✅ 更新 Python 依赖 `pip install -r requirements.txt`
6. ✅ 重启服务 `supervisorctl restart all`

---

## 故障排查

### 如果部署失败

1. **检查 GitHub Actions 日志**
   - 访问 Actions 页面查看详细错误信息

2. **检查 SSH 连接**
   ```bash
   # 在本地测试 SSH 连接
   ssh -i ~/.ssh/github_actions_key root@<SERVER_IP>
   ```

3. **检查服务器上的 Git 配置**
   ```bash
   cd /var/www/twitter-monitor
   git status
   git remote -v
   ```

4. **手动重启服务**
   ```bash
   supervisorctl restart all
   ```

---

## 安全建议

1. ✅ 使用专用的 SSH 密钥（不要使用个人密钥）
2. ✅ 定期轮换 SSH 密钥
3. ✅ 不要在代码中提交 .env 文件
4. ✅ 使用 GitHub Secrets 存储敏感信息

---

## 相关文档

- [DEPLOYMENT_GUIDE.md](./DEPLOYMENT_GUIDE.md) - 完整部署指南
- [MANUAL_DEPLOY_STEPS.md](./MANUAL_DEPLOY_STEPS.md) - 手动部署步骤
- [deploy_integrated.sh](./deploy_integrated.sh) - 整合部署脚本
- [ensure_uptime.sh](./ensure_uptime.sh) - 长期在线配置脚本
