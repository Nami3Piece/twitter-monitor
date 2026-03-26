#!/bin/bash
# Twitter Monitor 数据库双备份脚本
# 每天自动备份到云端和本地

set -e

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
SERVER="admin@43.103.0.20"
SSH_KEY="~/.ssh/id_aliyun"
REMOTE_DB="/var/www/twitter-monitor/data/tweets.db"
REMOTE_BACKUP_DIR="/var/www/twitter-monitor/backups"
LOCAL_BACKUP_DIR="$HOME/twitter-monitor/backups"

echo "=========================================="
echo "Twitter Monitor 数据库备份"
echo "时间: $(date)"
echo "=========================================="

# 1. 云端备份（在服务器上）
echo "1️⃣ 创建云端备份..."
ssh -i $SSH_KEY $SERVER "mkdir -p $REMOTE_BACKUP_DIR && cp $REMOTE_DB $REMOTE_BACKUP_DIR/tweets_$TIMESTAMP.db"
echo "✅ 云端备份完成: $REMOTE_BACKUP_DIR/tweets_$TIMESTAMP.db"

# 2. 下载到本地
echo "2️⃣ 下载到本地..."
mkdir -p $LOCAL_BACKUP_DIR
scp -i $SSH_KEY $SERVER:$REMOTE_DB $LOCAL_BACKUP_DIR/tweets_$TIMESTAMP.db
echo "✅ 本地备份完成: $LOCAL_BACKUP_DIR/tweets_$TIMESTAMP.db"

# 3. 清理旧备份（保留最近 7 天）
echo "3️⃣ 清理旧备份..."
# 云端清理
ssh -i $SSH_KEY $SERVER "find $REMOTE_BACKUP_DIR -name 'tweets_*.db' -mtime +7 -delete"
# 本地清理
find $LOCAL_BACKUP_DIR -name 'tweets_*.db' -mtime +7 -delete
echo "✅ 旧备份已清理（保留 7 天）"

# 4. 验证备份
echo "4️⃣ 验证备份..."
LOCAL_SIZE=$(ls -lh $LOCAL_BACKUP_DIR/tweets_$TIMESTAMP.db | awk '{print $5}')
echo "✅ 备份文件大小: $LOCAL_SIZE"

# 5. 备份用户数据库文档
echo "5️⃣ 备份用户数据库文档..."
cp $HOME/twitter-monitor/USER_DATABASE.md $LOCAL_BACKUP_DIR/USER_DATABASE_$TIMESTAMP.md
echo "✅ 用户数据库文档已备份"

echo ""
echo "=========================================="
echo "✅ 备份完成！"
echo "=========================================="
echo "云端备份: $REMOTE_BACKUP_DIR/tweets_$TIMESTAMP.db"
echo "本地备份: $LOCAL_BACKUP_DIR/tweets_$TIMESTAMP.db"
echo ""
echo "查看备份列表:"
echo "  云端: ssh -i $SSH_KEY $SERVER 'ls -lh $REMOTE_BACKUP_DIR'"
echo "  本地: ls -lh $LOCAL_BACKUP_DIR"
echo ""
