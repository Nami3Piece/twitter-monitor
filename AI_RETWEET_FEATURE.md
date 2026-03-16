# AI Retweet Draft Feature

## 功能概述

为 Twitter Monitor 添加了 AI 驱动的转发评论草稿生成功能，使用 Claude API 自动生成3种风格的转发评论。

## 核心功能

### 1. AI 草稿生成
- **3种风格选项**：
  - 💼 Professional（专业）：正式、有见地、行业聚焦
  - 😊 Casual（随意）：友好、对话式、易于理解
  - 🎉 Enthusiastic（热情）：兴奋、充满活力、支持性

### 2. 用户界面
- 每条推文卡片添加 "✨ AI Draft" 按钮
- 点击后弹出模态框显示生成的草稿
- 标签页切换不同风格
- 实时显示字符数统计
- 一键复制到剪贴板

### 3. 技术实现
- **后端 API**: `/api/ai-retweet-draft`
- **Claude 集成**: `ai/claude_retweet.py`
- **降级方案**: API 不可用时使用模板生成
- **字符限制**: 每条草稿 ≤ 240 字符

## 文件结构

```
twitter-monitor/
├── ai/
│   └── claude_retweet.py       # Claude API 集成模块
├── web.py                       # 添加了 API 端点和前端 UI
└── test_ai_draft.py            # 功能测试脚本
```

## 配置要求

在 `.env` 文件中配置：

```bash
# Claude API 配置
ANTHROPIC_API_KEY=sk-ant-xxx
ANTHROPIC_BASE_URL=https://code.newcli.com/claude/aws  # 可选，自定义代理
AI_ENABLED=1  # 启用 AI 功能
```

## 使用方法

1. **查看推文列表**
2. **点击推文卡片上的 "✨ AI Draft" 按钮**
3. **等待 AI 生成草稿**（约2-3秒）
4. **切换标签页查看不同风格**
5. **点击 "📋 Copy to Clipboard" 复制草稿**
6. **粘贴到 Twitter 进行转发**

## 降级策略

当 Claude API 不可用时，系统会自动使用模板生成草稿：
- 基于项目和关键词生成相关内容
- 添加项目特定的标签
- 保持3种风格的差异化

## 测试

运行测试脚本：

```bash
python3 test_ai_draft.py
```

## 技术特点

- ✅ 异步 API 调用，不阻塞主线程
- ✅ 错误处理和降级方案
- ✅ 响应式 UI 设计
- ✅ 字符数实时统计
- ✅ 剪贴板集成
- ✅ 模态框背景点击关闭

## 未来改进

- [ ] 支持自定义风格模板
- [ ] 添加草稿历史记录
- [ ] 支持编辑生成的草稿
- [ ] 多语言支持
- [ ] 批量生成草稿

## 版本历史

- **v1.0** (2026-03-16): 初始版本，支持3种风格的草稿生成
