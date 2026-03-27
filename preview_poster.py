"""
本地预览海报设计 — 运行后在 /tmp/poster_preview.png 查看效果
"""
import io
import os
import sys

# ── 字体路径（Mac 本地预览用）──────────────────────────────────────────────
# 服务器部署时将 FONT_GM 替换为下载好的 Playfair Display 路径
FONT_GM_PATH   = "/System/Library/Fonts/Supplemental/Didot.ttc"       # 艺术字 Good Morning (EN)
FONT_BOLD_PATH = "/System/Library/Fonts/Supplemental/Georgia Bold.ttf" # 副标题 (EN)
FONT_BODY_PATH = "/System/Library/Fonts/Supplemental/Georgia.ttf"      # 正文 (EN)
FONT_UI_PATH   = "/System/Library/Fonts/Helvetica.ttc"                 # 日期/badge/footer
FONT_CJK_PATH  = "/System/Library/Fonts/STHeiti Medium.ttc"            # 中文正文/副标题
FONT_CJK_BOLD  = "/System/Library/Fonts/STHeiti Medium.ttc"            # 中文粗体（STHeiti无独立Bold，用Medium）

# ── 调色板 ────────────────────────────────────────────────────────────────
_BG_WARM   = (252, 250, 248)
_PURPLE    = (109,  40, 217)
_PURPLE_LT = (237, 233, 254)
_PURPLE_DK = ( 76,  29, 149)
_GOLD      = (180,  90,   5)
_GOLD_SHADOW = (130, 60,   0)
_GOLD_LT   = (254, 243, 199)
_GOLD_BORDER = (217, 119,  6)
_INK       = ( 15,  23,  42)
_BODY      = ( 30,  41,  59)
_MUTED     = (100, 116, 139)
_BORDER    = (226, 232, 240)
_RULE      = (203, 213, 225)
_SECTION_BG = (245, 243, 255)   # 段落背景淡紫

W = 1080


def _get_font(path, size):
    from PIL import ImageFont
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def _tlen(draw, text, font):
    text = text.replace("\n", " ").strip()
    try:
        return draw.textlength(text, font=font)
    except Exception:
        bb = draw.textbbox((0, 0), text, font=font)
        return float(bb[2] - bb[0])


def _wrap(text, font, max_w, draw):
    """英文按词换行，中文按字换行。"""
    import unicodedata
    text = text.replace("\n", " ").strip()
    is_cjk = sum(1 for c in text if unicodedata.east_asian_width(c) in ('W', 'F')) > len(text) * 0.3
    if is_cjk:
        lines, cur = [], ""
        for ch in text:
            test = cur + ch
            if _tlen(draw, test, font) > max_w and cur:
                lines.append(cur); cur = ch
            else:
                cur = test
        if cur: lines.append(cur)
    else:
        words = text.split()
        lines, cur = [], ""
        for word in words:
            test = (cur + " " + word).strip()
            if _tlen(draw, test, font) > max_w and cur:
                lines.append(cur); cur = word
            else:
                cur = test
        if cur: lines.append(cur)
    return lines


def draw_poster(date, lang, text, out_path):
    from PIL import Image, ImageDraw

    MARGIN   = 72
    INDENT   = 20   # text indent after accent bar
    BAR_W    = 5    # left accent bar width
    BODY_W   = W - MARGIN * 2 - BAR_W - INDENT

    # ── 段落分割 ──────────────────────────────────────────────────────────
    raw_paras = [p.strip() for p in text.split('\n\n') if p.strip()]
    # 跳过首行 header（"📰 date · xxx"）
    paragraphs = []
    for p in raw_paras:
        first = p.split('\n')[0].strip()
        if first.startswith('📰') or (len(first) < 70 and '·' in first and len(p.split('\n')) == 1):
            continue
        paragraphs.append(p.replace('\n', ' ').strip())
    if not paragraphs:
        paragraphs = [text.strip()]

    # ── 字体（中英文分别选字体）────────────────────────────────────────────
    if lang == "zh":
        f_title = _get_font(FONT_CJK_BOLD,  64)   # 核心洞察 — 主标题
        f_brand = _get_font(FONT_CJK_PATH,  21)
        f_date  = _get_font(FONT_CJK_PATH,  21)
        f_num   = _get_font(FONT_CJK_PATH,  17)
        f_cta   = _get_font(FONT_CJK_PATH,  20)
        f_tag   = _get_font(FONT_CJK_PATH,  18)
    else:
        f_title = _get_font(FONT_BOLD_PATH, 62)   # Core Insight — Georgia Bold
        f_brand = _get_font(FONT_UI_PATH,   22)
        f_date  = _get_font(FONT_UI_PATH,   22)
        f_num   = _get_font(FONT_BOLD_PATH, 18)
        f_cta   = _get_font(FONT_UI_PATH,   21)
        f_tag   = _get_font(FONT_UI_PATH,   19)

    dummy = Image.new("RGB", (W, 100))
    dd    = ImageDraw.Draw(dummy)

    # ── 自动调整正文字号 ──────────────────────────────────────────────────
    PARA_GAP    = 40
    MAX_BODY_H  = 1050
    chosen_fs   = 30
    f_body      = None
    para_data   = []

    body_font_path = FONT_CJK_PATH if lang == "zh" else FONT_BODY_PATH
    for fs in (34, 30, 27, 24, 21, 18):
        fb = _get_font(body_font_path, fs)
        bb = dd.textbbox((0, 0), "Ag测", font=fb)
        lh = bb[3] - bb[1] + 10
        pdata = [_wrap(p, fb, BODY_W, dd) for p in paragraphs]
        total_h = sum(len(ls) * lh for ls in pdata) + (len(pdata) - 1) * PARA_GAP
        if total_h <= MAX_BODY_H:
            chosen_fs = fs; f_body = fb; para_data = pdata
            break
    if f_body is None:
        f_body = _get_font(body_font_path, 18)
        para_data = [_wrap(p, f_body, BODY_W, dd) for p in paragraphs]

    bb     = dd.textbbox((0, 0), "Ag测", font=f_body)
    line_h = bb[3] - bb[1] + 10
    body_h = sum(len(ls) * line_h for ls in para_data) + (len(para_data) - 1) * PARA_GAP
    # 每段额外加 padding（背景块上下）
    SECTION_PAD = 18
    body_h += len(para_data) * SECTION_PAD * 2

    # ── 画布高度 ──────────────────────────────────────────────────────────
    TOP_PAD    = 64
    TITLE_H    = 90    # 核心洞察 / Core Insight 主标题行高
    DATE_H     = 50
    RULE1_H    = 36
    BODY_PAD_T = 36
    BODY_PAD_B = 52
    CTA_H      = 80
    RULE2_H    = 30
    TAG_H      = 56
    BOT_PAD    = 52

    canvas_h = (TOP_PAD + TITLE_H + DATE_H + RULE1_H
                + BODY_PAD_T + body_h + BODY_PAD_B
                + CTA_H + RULE2_H + TAG_H + BOT_PAD)
    canvas_h += canvas_h % 2

    img  = Image.new("RGB", (W, canvas_h), _BG_WARM)
    draw = ImageDraw.Draw(img)

    # ── 顶部装饰条（渐变感：三段） ────────────────────────────────────────
    draw.rectangle([0, 0, W // 3, 8],       fill=_PURPLE_DK)
    draw.rectangle([W // 3, 0, W * 2 // 3, 8], fill=_PURPLE)
    draw.rectangle([W * 2 // 3, 0, W, 8],   fill=(168, 85, 247))  # purple-400

    y = TOP_PAD

    # ── 主标题：核心洞察 / Core Insight ───────────────────────────────────
    title_text = "核心洞察" if lang == "zh" else "Core Insight"
    draw.text((MARGIN, y), title_text, font=f_title, fill=_PURPLE)

    # Brand badge（右上，与标题垂直居中）
    badge = "Daily X Digest"
    bw = _tlen(draw, badge, f_brand)
    bx = W - MARGIN - int(bw) - 24
    by = y + 16
    draw.rounded_rectangle([bx - 14, by, bx + int(bw) + 14, by + 34], radius=17, fill=_PURPLE_LT)
    draw.text((bx, by + 7), badge, font=f_brand, fill=_PURPLE)

    y += TITLE_H

    # ── 日期 ─────────────────────────────────────────────────────────────
    draw.text((MARGIN, y), date, font=f_date, fill=_MUTED)
    y += DATE_H

    # ── 分隔线 ───────────────────────────────────────────────────────────
    draw.rectangle([MARGIN, y, W - MARGIN, y + 2], fill=_RULE)
    y += RULE1_H + BODY_PAD_T

    # ── 章节段落 ──────────────────────────────────────────────────────────
    section_labels_en = ["Overview", "Deep Dive", "Watch Next"]
    section_labels_zh = ["综合信号", "重点分析", "关注要点"]

    for idx, lines in enumerate(para_data):
        sec_h = len(lines) * line_h + SECTION_PAD * 2

        # 段落背景（淡紫圆角块）
        draw.rounded_rectangle(
            [MARGIN, y, W - MARGIN, y + sec_h],
            radius=10, fill=_SECTION_BG
        )

        # 左侧紫色竖条
        draw.rounded_rectangle(
            [MARGIN, y, MARGIN + BAR_W, y + sec_h],
            radius=10, fill=_PURPLE
        )

        # 章节编号标签（右上角）
        labels = section_labels_zh if lang == "zh" else section_labels_en
        label  = labels[idx] if idx < len(labels) else f"§{idx+1}"
        lw     = _tlen(draw, label, f_num)
        draw.text(
            (W - MARGIN - int(lw) - 16, y + SECTION_PAD - 2),
            label, font=f_num, fill=_PURPLE
        )

        # 正文
        ty = y + SECTION_PAD
        for line in lines:
            draw.text((MARGIN + BAR_W + INDENT, ty), line, font=f_body, fill=_BODY)
            ty += line_h

        y += sec_h
        if idx < len(para_data) - 1:
            y += PARA_GAP

    # ── CTA 胶囊 ─────────────────────────────────────────────────────────
    y += BODY_PAD_B
    draw.rounded_rectangle([MARGIN, y, W - MARGIN, y + 64], radius=32, fill=_GOLD_LT)
    draw.rounded_rectangle([MARGIN, y, W - MARGIN, y + 64], radius=32, outline=_GOLD_BORDER, width=1)
    cta = ("🌐  monitor.dailyxdigest.uk  ·  免费订阅  ·  早八点准时播报" if lang == "zh"
           else "🌐  monitor.dailyxdigest.uk  ·  Free  ·  Daily UTC 0:00")
    cta_w = _tlen(draw, cta, f_cta)
    draw.text((W // 2 - int(cta_w) // 2, y + 18), cta, font=f_cta, fill=_GOLD)

    # ── 底部分隔 + tagline ────────────────────────────────────────────────
    y += CTA_H + RULE2_H
    draw.rectangle([MARGIN, y, W - MARGIN, y + 1], fill=_BORDER)
    y += 20
    tagline = ("⚠️ 以上内容仅供参考，不构成任何投资建议。投资有风险，决策需谨慎。" if lang == "zh"
               else "⚠️ For reference only. Not financial advice. Invest at your own risk.")
    tl_w = _tlen(draw, tagline, f_tag)
    draw.text((W // 2 - int(tl_w) // 2, y), tagline, font=f_tag, fill=_MUTED)

    # ── 底部装饰条 ────────────────────────────────────────────────────────
    draw.rectangle([0, canvas_h - 8, W * 2 // 3, canvas_h], fill=_PURPLE)
    draw.rectangle([W * 2 // 3, canvas_h - 8, W, canvas_h], fill=(168, 85, 247))

    img.save(out_path, format="PNG", optimize=True)
    print(f"✅ 保存到: {out_path}  ({W}×{canvas_h}px)")


# ── 测试内容 ──────────────────────────────────────────────────────────────
SAMPLE_EN = """📰 2026-03-27 · Core Intelligence

A unifying narrative is crystallizing across all four monitored sectors today: the market is decisively rewarding systems that are verifiable, measurable, and auditable over those that remain theoretical. ARKREEN's 300 kW solar node going live in Thailand with on-chain energy verification, Morgan Stanley listing a Bitcoin ETF on the NYSE, TLAY ecosystem participants articulating multi-layer traceability stacks, and Grok 4.20 claiming the third global spot on Web App Arena benchmarks all point to the same structural shift — from "trust me" to "verify me."

For ARKREEN specifically, the Thailand node deployment marks a critical inflection point. The project's explicit rejection of "theoretical pitch decks" signals that the DePIN sector is entering a brutal selection phase where only projects demonstrating real physical infrastructure with on-chain data transparency will survive. Meanwhile, the GreenBTC ecosystem faces an intensifying cost crisis: Bitcoin mining costs approaching $79,995 per coin, with Marathon Digital liquidating over 15,000 BTC from its treasury.

The critical watchpoints ahead sit at two intersections of risk and opportunity. First, institutional adoption is accelerating at an unprecedented pace — Australia's central bank integrating crypto directly into its banking system, combined with Coinbase launching Bitcoin-collateralized mortgages for 120 million Americans. Second, the AI competitive landscape is undergoing violent reordering: Grok 4.20 surpassing Claude Opus 4.5 and Gemini 3.1 Pro — capability explosion and regulatory tightening are happening simultaneously."""

SAMPLE_ZH = """📰 2026-03-27 · 今日核心判断

今日四个监测板块呈现出一个共同的底层叙事：基础设施的"可验证化"正在从概念走向落地，而传统机构则在加速拥抱链上资产。无论是ARKREEN在泰国上线的300kW太阳能节点强调"链上可验证能源生产"，还是摩根斯丹利获批在纽交所挂牌比特币ETF $MSBT，抑或是TLAY生态中Benefer所描述的区块链+大数据+物联网+AI协同的溯源网络，再到AI Renaissance领域Grok 4.20在Web App Arena基准测试中跻身全球前三——所有信号都指向同一个方向。

对ARKREEN而言，泰国300kW太阳能节点的上线是一个关键里程碑。项目方明确表态"我们已经不再做理论性的路演文档"，这意味着DePIN赛道正在经历残酷的筛选期——只有能展示真实物理基础设施运转数据的项目才能存活。与此同时，澳大利亚正在建设价值一亿美元的特斯拉Megapack储能设施，南非Eskom延长居民太阳能光伏和电池储能系统的注册截止日期，全球范围内分布式能源的政策窗口和市场需求正在同步打开。

接下来需要密切关注两个风险与机遇交汇点：第一，澳大利亚央行将比特币和加密货币直接整合进银行系统并称之为"不可逆转的趋势"，叠加Coinbase面向1.2亿美国人推出比特币抵押贷款——机构采用速度的加快可能在短期内制造流动性虹吸效应。第二，AI领域的竞争格局正在剧烈重组，Grok 4.20超越Claude Opus 4.5和Gemini 3.1 Pro，技术能力的爆发与监管框架的收紧正在同步发生。"""


if __name__ == "__main__":
    lang = sys.argv[1] if len(sys.argv) > 1 else "en"
    text = SAMPLE_ZH if lang == "zh" else SAMPLE_EN
    out  = f"/tmp/poster_preview_{lang}.png"
    draw_poster("2026-03-27", lang, text, out)
    os.system(f"open {out}")
