# 如何添加 GitHub Actions Workflow

由于当前的 GitHub token 没有 `workflow` 权限，需要手动在 GitHub 网页上创建 workflow 文件。

## 方法 1: 在 GitHub 网页上创建（推荐）

1. 访问：https://github.com/Nami3Piece/twitter-monitor

2. 点击 "Add file" → "Create new file"

3. 文件路径输入：`.github/workflows/deploy.yml`

4. 复制以下内容到文件中：

```yaml
name: Deploy to Aliyun Server

on:
  push:
    branches: [ main ]
  workflow_dispatch:

jobs:
  deploy:
    runs-on: ubuntu-latest

    steps:
    - name: Checkout code
      uses: actions/checkout@v3

    - name: Deploy to server
      uses: appleboy/ssh-action@master
      with:
        host: ${{ secrets.SERVER_HOST }}
        username: ${{ secrets.SERVER_USER }}
        key: ${{ secrets.SERVER_SSH_KEY }}
        script: |
          # 进入项目目录
          cd /var/www/twitter-monitor

          # 拉取最新代码
          git pull origin main

          # 激活虚拟环境并更新依赖
          source venv/bin/activate
          pip install -r requirements.txt

          # 重启服务
          supervisorctl restart all

          echo "✅ Twitter Monitor 部署完成"
```

5. 点击 "Commit new file"

## 方法 2: 更新 GitHub Token

如果您想通过命令行推送 workflow 文件：

1. 访问：https://github.com/settings/tokens

2. 点击 "Generate new token" → "Generate new token (classic)"

3. 勾选以下权限：
   - ✅ repo (所有子选项)
   - ✅ workflow

4. 生成 token 后，更新本地 git 配置：

```bash
git remote set-url origin https://YOUR_USERNAME:YOUR_NEW_TOKEN@github.com/Nami3Piece/twitter-monitor.git
```

5. 然后推送 workflow 文件：

```bash
git add .github/workflows/deploy.yml
git commit -m "Add GitHub Actions workflow"
git push origin main
```

## 完成后的步骤

创建 workflow 文件后，按照 `GITHUB_ACTIONS_SETUP.md` 中的步骤配置 GitHub Secrets：

1. 在服务器上生成 SSH 密钥
2. 在 GitHub 添加 3 个 secrets (SERVER_HOST, SERVER_USER, SERVER_SSH_KEY)
3. 测试自动部署

## 本地 workflow 文件位置

workflow 文件已保存在本地：
- 路径：`.github/workflows/deploy.yml`
- 您可以随时查看或修改此文件
