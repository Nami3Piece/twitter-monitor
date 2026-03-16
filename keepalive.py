#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Render 服务保活脚本
使用 cron 定期 ping 服务，防止休眠
"""

import requests
import time
from datetime import datetime

# 你的 Render 服务 URL
SERVICE_URL = "https://monitor.dailyxdigest.uk"

def ping_service():
    """Ping 服务保持在线"""
    try:
        # 使用首页而不是 API 端点（不需要认证）
        response = requests.get(SERVICE_URL, timeout=30)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if response.status_code == 200:
            print(f"[{timestamp}] ✅ Ping 成功 - 状态码: {response.status_code}")
        else:
            print(f"[{timestamp}] ⚠️  Ping 响应异常 - 状态码: {response.status_code}")

    except Exception as e:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] ❌ Ping 失败: {e}")

if __name__ == "__main__":
    ping_service()
