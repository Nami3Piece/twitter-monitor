#!/bin/bash
# 从服务器同步最新备份到本地
REMOTE="admin@43.103.0.20"
REMOTE_BACKUP_DIR="/var/www/twitter-monitor/backups"
LOCAL_BACKUP_DIR="$HOME/twitter-monitor/backups/remote"
SSH_KEY="$HOME/.ssh/id_aliyun"
LOG="$LOCAL_BACKUP_DIR/sync.log"

mkdir -p "$LOCAL_BACKUP_DIR"

# 获取服务器最新备份文件名
LATEST=$(ssh -i "$SSH_KEY" "$REMOTE" "ls -t $REMOTE_BACKUP_DIR/tweets_*.db 2>/dev/null | head -1")

if [ -z "$LATEST" ]; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') ERROR: no backup found on server" >> "$LOG"
  exit 1
fi

FILENAME=$(basename "$LATEST")
LOCAL_FILE="$LOCAL_BACKUP_DIR/$FILENAME"

if [ -f "$LOCAL_FILE" ]; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') already exists: $FILENAME" >> "$LOG"
  exit 0
fi

scp -i "$SSH_KEY" "$REMOTE:$LATEST" "$LOCAL_FILE"
echo "$(date '+%Y-%m-%d %H:%M:%S') synced: $FILENAME" >> "$LOG"

# 只保留最近 7 个本地备份
ls -t "$LOCAL_BACKUP_DIR"/tweets_*.db 2>/dev/null | tail -n +8 | xargs -r rm
