#!/usr/bin/env python3
"""
自动触发 Render 部署
"""
import requests
import sys

def trigger_deploy(api_key, service_id):
    """触发 Render 服务部署"""
    url = f"https://api.render.com/v1/services/{service_id}/deploys"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    response = requests.post(url, headers=headers, json={})

    if response.status_code == 201:
        print("✅ 部署已触发")
        deploy = response.json()
        print(f"📦 Deploy ID: {deploy.get('id')}")
        print(f"🔗 查看进度: https://dashboard.render.com/")
        return True
    else:
        print(f"❌ 部署失败: {response.status_code}")
        print(response.text)
        return False

def get_service_id(api_key, service_name="twitter-monitor"):
    """获取服务 ID"""
    url = "https://api.render.com/v1/services"
    headers = {"Authorization": f"Bearer {api_key}"}

    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        services = response.json()
        for service in services:
            if service.get('service', {}).get('name') == service_name:
                return service['service']['id']
    return None

if __name__ == "__main__":
    print("🚀 Render 自动部署工具\n")
    print("需要 Render API Key 来触发部署")
    print("获取方式: https://dashboard.render.com/u/settings/api-keys\n")

    api_key = input("请输入 Render API Key: ").strip()

    if not api_key:
        print("❌ 未提供 API Key")
        sys.exit(1)

    print("\n🔍 查找服务...")
    service_id = get_service_id(api_key)

    if not service_id:
        print("❌ 未找到 twitter-monitor 服务")
        print("请手动输入 Service ID (在 Render 控制台 Settings 中找到)")
        service_id = input("Service ID: ").strip()

    if service_id:
        print(f"✅ 找到服务: {service_id}")
        print("\n🚀 触发部署...")
        trigger_deploy(api_key, service_id)
    else:
        print("❌ 无法获取 Service ID")
