#!/usr/bin/env python3
"""
Weekly X Algorithm Report
Runs every Monday 9:00 AM — searches for X algorithm updates,
generates a Chinese summary via Claude API, pushes to GitHub.
"""
import os, sys, json, base64, datetime, requests
from pathlib import Path

# ── Load .env ─────────────────────────────────────────────────────────────────
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

ANTHROPIC_API_KEY  = os.environ["ANTHROPIC_API_KEY"]
ANTHROPIC_BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
GITHUB_TOKEN       = os.environ["GITHUB_TOKEN"]
GITHUB_REPO        = "Nami3Piece/twitter-monitor"

today     = datetime.date.today()
week_str  = today.strftime("%Y-W%V")   # e.g. 2026-W13
date_str  = today.strftime("%Y-%m-%d")
year_str  = today.strftime("%Y")

# ── Call Claude API (web_search tool) ─────────────────────────────────────────
def call_claude(prompt: str) -> str:
    url = f"{ANTHROPIC_BASE_URL.rstrip('/')}/v1/messages"
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 4096,
        "tools": [{
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": 5
        }],
        "messages": [{"role": "user", "content": prompt}]
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    # Extract text from content blocks
    texts = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
    return "\n".join(texts)

# ── Generate report ───────────────────────────────────────────────────────────
prompt = f"""
今天是 {date_str}，请搜索并整理本周（过去7天）关于 X (Twitter) 推荐算法的最新动态。

搜索以下几个方向：
1. Elon Musk 关于 X 算法的最新公告或推文
2. Grok AI 推荐系统的更新（搜索 "X Grok recommendation algorithm {year_str}"）
3. twitter/the-algorithm GitHub 仓库的最新活动
4. X Engineering Blog 或 xAI 的技术文章
5. 行业媒体对 X 算法变化的报道（Social Media Today, TechCrunch 等）

然后输出一份中文周报，格式严格如下（Markdown）：

---
# X 算法周报 · {week_str}

> 生成日期：{date_str} · 数据来源：公开信息搜索

## 本周重要动态

[列出 3-5 条本周最重要的算法相关动态，无更新则写"本周无重大公告"]

## 信号变化分析

[分析这些变化对内容创作者和内容推荐系统的影响，具体说明哪些信号权重可能发生变化]

## 对 Daily X Digest 的策略建议

[针对我们的三个项目（ARKREEN能源上链、GREENBTC绿色比特币、TLAY物理AI），分析这些算法变化是否影响我们的内容筛选策略，给出具体建议]

## 值得关注的趋势

[1-3条中长期趋势观察]

## 参考来源

[列出所有参考链接]
---

请确保内容准确，有根有据，不要编造信息。如果本周没有重大变化，如实说明。
"""

print(f"[{date_str}] Generating weekly X algorithm report...")
try:
    report_content = call_claude(prompt)
except Exception as e:
    print(f"Claude API error: {e}")
    # Fallback: minimal report
    report_content = f"""# X 算法周报 · {week_str}

> 生成日期：{date_str} · 自动生成失败，请手动更新

## 本周动态

本周自动搜索失败（API 错误：{e}），请访问以下链接手动查阅：

- https://blog.x.com/engineering
- https://github.com/twitter/the-algorithm
- https://www.socialmediatoday.com/c/social-networks/x-twitter/

## 参考来源

- [X Engineering Blog](https://blog.x.com/engineering)
- [twitter/the-algorithm](https://github.com/twitter/the-algorithm)
"""

# ── Push to GitHub ────────────────────────────────────────────────────────────
file_path = f"docs/weekly-reports/{year_str}/{week_str}.md"
api_url   = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{file_path}"

headers = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json"
}

# Check if file already exists (to get SHA for update)
sha = None
existing = requests.get(api_url, headers=headers)
if existing.status_code == 200:
    sha = existing.json()["sha"]

encoded = base64.b64encode(report_content.encode("utf-8")).decode("ascii")
payload = {
    "message": f"report: X algorithm weekly report {week_str}",
    "content": encoded,
}
if sha:
    payload["sha"] = sha

resp = requests.put(api_url, headers=headers, json=payload)
resp.raise_for_status()

html_url = resp.json()["content"]["html_url"]
print(f"✅ Report published: {html_url}")
