#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
服务监控脚本 - 带 Telegram 通知
检查服务状态，宕机时发送 Telegram 通知
"""

import requests
import time
from datetime import datetime
from pathlib import Path

# 配置
SERVICE_URL = "https://monitor.dailyxdigest.uk/"
TELEGRAM_BOT_TOKEN = "8634326385:AAHZd94LIxX6xQXrsKrU3a2h8kWGJNtZr9g"
TELEGRAM_CHAT_ID = "-5246268118"

# 状态文件
STATUS_FILE = Path.home() / "twitter-monitor" / "monitor_status.txt"

def send_telegram_message(message):
    """发送 Telegram 消息"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }
        response = requests.post(url, json=data, timeout=10)
        return response.status_code == 200
    except Exception as e:
        print(f"发送 Telegram 消息失败: {e}")
        return False

def get_last_status():
    """获取上次的服务状态"""
    try:
        if STATUS_FILE.exists():
            return STATUS_FILE.read_text().strip()
    except:
        pass
    return "unknown"

def save_status(status):
    """保存当前服务状态"""
    try:
        STATUS_FILE.write_text(status)
    except:
        pass

def check_service():
    """检查服务状态"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    last_status = get_last_status()

    try:
        response = requests.get(SERVICE_URL, timeout=30)

        if response.status_code == 200:
            print(f"[{timestamp}] ✅ 服务正常 - 状态码: {response.status_code}")

            # 如果之前是宕机状态，发送恢复通知
            if last_status == "down":
                message = f"🟢 <b>服务已恢复</b>\n\n"
                message += f"🌐 URL: {SERVICE_URL}\n"
                message += f"⏰ 时间: {timestamp}\n"
                message += f"✅ 状态: 正常运行"
                send_telegram_message(message)
                print(f"[{timestamp}] 📱 已发送恢复通知")

            save_status("up")
            return True

        else:
            print(f"[{timestamp}] ⚠️  服务异常 - 状态码: {response.status_code}")

            # 如果之前是正常状态，发送宕机通知
            if last_status != "down":
                message = f"🔴 <b>服务宕机警告</b>\n\n"
                message += f"🌐 URL: {SERVICE_URL}\n"
                message += f"⏰ 时间: {timestamp}\n"
                message += f"❌ 状态码: {response.status_code}\n"
                message += f"⚠️  请检查服务状态"
                send_telegram_message(message)
                print(f"[{timestamp}] 📱 已发送宕机通知")

            save_status("down")
            return False

    except Exception as e:
        print(f"[{timestamp}] ❌ 检查失败: {e}")

        # 如果之前是正常状态，发送宕机通知
        if last_status != "down":
            message = f"🔴 <b>服务无法访问</b>\n\n"
            message += f"🌐 URL: {SERVICE_URL}\n"
            message += f"⏰ 时间: {timestamp}\n"
            message += f"❌ 错误: {str(e)}\n"
            message += f"⚠️  请检查服务状态"
            send_telegram_message(message)
            print(f"[{timestamp}] 📱 已发送宕机通知")

        save_status("down")
        return False

if __name__ == "__main__":
    check_service()
