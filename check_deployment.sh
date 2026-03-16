#!/bin/bash
# 检查 Render 部署状态

echo "🔍 检查服务状态..."
echo ""

# 检查服务是否在线
STATUS=$(curl -s -o /dev/null -w "%{http_code}" https://monitor.dailyxdigest.uk/)

if [ "$STATUS" = "200" ]; then
    echo "✅ 服务在线 (HTTP $STATUS)"
else
    echo "⚠️  服务状态异常 (HTTP $STATUS)"
fi

echo ""
echo "📋 下一步操作："
echo ""
echo "1. 访问 Render 控制台: https://dashboard.render.com/"
echo "2. 找到你的 twitter-monitor 服务"
echo "3. 检查是否有新的部署正在进行"
echo "4. 如果没有自动部署，点击 'Manual Deploy' → 'Deploy latest commit'"
echo ""
echo "🔗 GitHub 仓库: https://github.com/Nami3Piece/twitter-monitor"
echo "🌐 服务地址: https://monitor.dailyxdigest.uk/"
echo ""
echo "✨ 新功能："
echo "  - All/项目标签页：隐藏 AI Retweet Draft 列"
echo "  - Voted 标签页：显示 3 个 Quote + 3 个Comment 版本"
echo "  - Vote 时自动生成 AI 互动内容"
