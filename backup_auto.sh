#!/bin/bash
BACKUP_DIR=/var/www/twitter-monitor/backups
DB_PATH=/var/www/twitter-monitor/data/tweets.db
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE=$BACKUP_DIR/tweets_$DATE.db

cp $DB_PATH $BACKUP_FILE
echo "$(date '+%Y-%m-%d %H:%M:%S') backup ok: $BACKUP_FILE" >> $BACKUP_DIR/backup.log

# 只保留最近 7 个备份
ls -t $BACKUP_DIR/tweets_*.db | tail -n +8 | xargs -r rm
echo "$(date '+%Y-%m-%d %H:%M:%S') cleanup done" >> $BACKUP_DIR/backup.log
