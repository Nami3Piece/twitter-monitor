#!/bin/bash
# 自动部署到 Render（在添加支付信息后运行）

API_KEY="rnd_9kfvpHUcgnD7P5O9nWTpz4Rejgu6"
OWNER_ID="tea-d6rnv5ffte5s73eqkuqg"

echo "🚀 创建 Render 服务..."

SERVICE_ID=$(curl -s -X POST https://api.render.com/v1/services \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "web_service",
    "name": "twitter-monitor",
    "ownerId": "'"$OWNER_ID"'",
    "repo": "https://github.com/Nami3Piece/twitter-monitor",
    "autoDeploy": "yes",
    "branch": "main",
    "serviceDetails": {
      "env": "python",
      "region": "singapore",
      "plan": "free",
      "pullRequestPreviewsEnabled": "no",
      "envSpecificDetails": {
        "buildCommand": "pip install -r requirements.txt",
        "startCommand": "python3 main.py"
      }
    }
  }' | python3 -c "import sys, json; data=json.load(sys.stdin); print(data.get('service', {}).get('id', ''))")

if [ -n "$SERVICE_ID" ]; then
    echo "✅ 服务创建成功！"
    echo "📦 Service ID: $SERVICE_ID"
    echo ""
    echo "⚠️  下一步：添加环境变量"
    echo "访问: https://dashboard.render.com/web/$SERVICE_ID/env-vars"
    echo ""
    echo "需要添加的环境变量："
    echo "  - ANTHROPIC_API_KEY"
    echo "  - WEB_USER / WEB_PASSWORD"
    echo "  - TWITTER_BEARER_TOKEN"
    echo "  - TWITTER_API_KEY / TWITTER_API_SECRET"
    echo "  - TWITTER_ACCESS_TOKEN / TWITTER_ACCESS_SECRET"
    echo "  - TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID"
else
    echo "❌ 创建失败"
    echo "请先访问 https://dashboard.render.com/billing 添加支付信息"
fi
