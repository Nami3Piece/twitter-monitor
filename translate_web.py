#!/usr/bin/env python3
"""
Translate web.py from Chinese to English
"""

translations = {
    # Common UI elements
    "个关键词": " keywords",
    "条推文": " tweets",
    "个项目": " projects",
    "已投票": "Voted",
    "追踪账号": "Tracked Accounts",
    "已关注": "Following",
    "热点事件": "Hot Events",
    "过去24小时": "Last 24 hours",
    "按互动量排序": "Sorted by engagement",
    "查看原推": "View Tweet",
    "合适": "Suitable",
    "已选": "Selected",
    "投票": "Vote",
    "操作": "Actions",
    "关键词": "Keyword",
    "推文": "Tweet",
    "转推草稿": "Retweet Draft",
    "全选": "Select All",
    "批量删除选中": "Delete Selected",

    # Room of Requirement
    "有求必应屋": "Contribution Hub",
    "觉得我们的关键词不够": "Think our keywords are limited",
    "分享你今天看到的热帖": "Share today's hot posts",
    "帮我们拓宽视野": "and broaden our horizons",
    "分享内容": "Share Content",
    "支持": "Supports",
    "链接": "links",
    "新闻链接或关键词": "news links, or keywords",
    "粘贴链接或输入关键词": "Paste link or enter keywords",
    "分析": "Analyze",
    "或": "or",
    "手动添加关键词": "Manual Add Keywords",
    "选择项目": "Select Project",
    "输入关键词": "Enter keyword",
    "添加到项目": "Add to Project",
    "添加中": "Adding",
    "感谢您的贡献": "Thank You for Your Contribution",
    "感谢您分享今天看到的热帖并提供关键词": "Thank you for sharing and contributing the keyword",
    "请": "Please come back in",
    "小时后": " hours",
    "回来": "",
    "您会看到关键词相关的新闻": "to see news related to this keyword",
    "您的贡献将显示绿色心标记": "Your contribution will be marked with a green heart",
    "好的，我知道了": "Got it",
    "社区贡献": "Community Contribution",
    "关键词监控统计": "Keyword Statistics",
    "共": "Total",
    "内": " in ",

    # Messages
    "关键词已添加": "Keyword added",
    "需要重启服务生效": "Service restart required",
    "关键词已存在": "Keyword already exists",
    "添加失败": "Failed to add",
    "请选择项目": "Please select a project",
    "请输入关键词": "Please enter a keyword",
    "网络错误": "Network error",
    "分析失败": "Analysis failed",
    "请重试": "Please retry",
    "未找到相关关键词": "No relevant keywords found",
    "推荐的关键词": "Recommended Keywords",
    "推荐理由": "Reason",
    "添加": "Add",
    "跳过": "Skip",

    # Stats
    "24h 推文": "24h Tweets",
    "追踪账号": "Accounts",
    "已关注": "Following",

    # Time
    "每8小时自动抓取": "Auto-fetch every 8 hours",
    "更新时间": "Updated",
    "仅显示过去": "Showing last",
    "自动刷新": "Auto-refresh",

    # Buttons
    "确定删除这条推文": "Delete this tweet",
    "确定删除选中的": "Delete selected",

    # Projects
    "能源": "Energy",
    "绿色比特币": "Green Bitcoin",
    "机器经济": "Machine Economy",
    "工具": "Tools",
}

def translate_file(input_file, output_file):
    with open(input_file, 'r', encoding='utf-8') as f:
        content = f.read()

    # Apply translations
    for chinese, english in translations.items():
        content = content.replace(chinese, english)

    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f"Translation complete: {input_file} -> {output_file}")

if __name__ == "__main__":
    translate_file('/Users/namipieces/twitter-monitor/web.py',
                   '/Users/namipieces/twitter-monitor/web_en.py')
