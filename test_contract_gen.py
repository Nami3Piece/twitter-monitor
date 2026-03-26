"""
test_contract_gen.py — 功能与性能测试
"""
import sys
import os
import time

sys.path.insert(0, "/Users/namipieces/twitter-monitor")
from contract_gen import generate_contract, _build_data

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"

results = []

def run(name, fn):
    t0 = time.perf_counter()
    try:
        ok, detail = fn()
        elapsed = time.perf_counter() - t0
        status = PASS if ok else FAIL
        print(f"[{status}] {name}  ({elapsed*1000:.0f}ms){('  => ' + detail) if detail else ''}")
        results.append((name, ok, elapsed))
    except Exception as e:
        elapsed = time.perf_counter() - t0
        print(f"[{FAIL}] {name}  ({elapsed*1000:.0f}ms)  => EXCEPTION: {e}")
        results.append((name, False, elapsed))

# ── 基础产品 ──────────────────────────────────────────────────────────────────

SINGLE = {
    "buyer_name": "Test Buyer",
    "buyer_address": "123 Test St",
    "buyer_contact": "test@example.com",
    "products": [{"name": "Widget A", "sku": "WA-001", "qty": 2, "unit_price": 100}],
}

MULTI = {
    "buyer_name": "Test Buyer",
    "buyer_address": "123 Test St",
    "buyer_contact": "test@example.com",
    "products": [
        {"name": "Widget A", "sku": "WA-001", "qty": 2, "unit_price": 100},
        {"name": "Widget B", "sku": "WB-002", "qty": 5, "unit_price": 50},
        {"name": "Widget C", "sku": "WC-003", "qty": 1, "unit_price": 999},
    ],
}

# ── 1. 单产品 both/both ───────────────────────────────────────────────────────
def t_single_both_both():
    r = generate_contract({**SINGLE, "lang": "both", "format": "both"})
    keys = set(r.keys())
    ok = keys == {"cn_pdf", "en_pdf", "cn_docx", "en_docx"} and all(os.path.exists(v) for v in r.values())
    return ok, f"keys={keys}" if not ok else ""

run("单产品 lang=both format=both → 4文件", t_single_both_both)

# ── 2. 多产品 both/both ───────────────────────────────────────────────────────
def t_multi_both_both():
    r = generate_contract({**MULTI, "lang": "both", "format": "both"})
    ok = len(r) == 4 and all(os.path.exists(v) for v in r.values())
    return ok, ""

run("多产品 lang=both format=both → 4文件", t_multi_both_both)

# ── 3. lang=cn ────────────────────────────────────────────────────────────────
def t_lang_cn():
    r = generate_contract({**SINGLE, "lang": "cn", "format": "both"})
    ok = set(r.keys()) == {"cn_pdf", "cn_docx"}
    return ok, f"keys={set(r.keys())}"

run("lang=cn → 只有 cn_pdf + cn_docx", t_lang_cn)

# ── 4. lang=en ────────────────────────────────────────────────────────────────
def t_lang_en():
    r = generate_contract({**SINGLE, "lang": "en", "format": "both"})
    ok = set(r.keys()) == {"en_pdf", "en_docx"}
    return ok, f"keys={set(r.keys())}"

run("lang=en → 只有 en_pdf + en_docx", t_lang_en)

# ── 5. format=pdf ─────────────────────────────────────────────────────────────
def t_fmt_pdf():
    r = generate_contract({**SINGLE, "lang": "both", "format": "pdf"})
    ok = set(r.keys()) == {"cn_pdf", "en_pdf"}
    return ok, f"keys={set(r.keys())}"

run("format=pdf → 只有 cn_pdf + en_pdf", t_fmt_pdf)

# ── 6. format=docx ────────────────────────────────────────────────────────────
def t_fmt_docx():
    r = generate_contract({**SINGLE, "lang": "both", "format": "docx"})
    ok = set(r.keys()) == {"cn_docx", "en_docx"}
    return ok, f"keys={set(r.keys())}"

run("format=docx → 只有 cn_docx + en_docx", t_fmt_docx)

# ── 7. spec_text > 20字 触发规格章节 ─────────────────────────────────────────
def t_spec_long():
    params = {**SINGLE,
              "lang": "cn", "format": "pdf",
              "products": [{**SINGLE["products"][0],
                            "spec_text": "这是一段超过二十个字的产品规格说明文字，用于触发规格章节"}]}
    d = _build_data(params)
    ok = d["needs_spec"] is True
    return ok, f"needs_spec={d['needs_spec']}"

run("spec_text>20字 → needs_spec=True", t_spec_long)

# ── 8. spec_text ≤ 20字 不触发 ───────────────────────────────────────────────
def t_spec_short():
    params = {**SINGLE,
              "products": [{**SINGLE["products"][0], "spec_text": "短规格"}]}
    d = _build_data(params)
    ok = d["needs_spec"] is False
    return ok, f"needs_spec={d['needs_spec']}"

run("spec_text≤20字 → needs_spec=False", t_spec_short)

# ── 9. 无规格字段 不触发 ──────────────────────────────────────────────────────
def t_spec_none():
    d = _build_data(SINGLE)
    ok = d["needs_spec"] is False
    return ok, f"needs_spec={d['needs_spec']}"

run("无 spec_text → needs_spec=False", t_spec_none)

# ── 10. spec_text 恰好 20字 不触发 ───────────────────────────────────────────
def t_spec_exactly20():
    params = {**SINGLE,
              "products": [{**SINGLE["products"][0], "spec_text": "一二三四五六七八九十一二三四五六七八九十"}]}
    d = _build_data(params)
    ok = d["needs_spec"] is False
    return ok, f"needs_spec={d['needs_spec']}, len={len(params['products'][0]['spec_text'])}"

run("spec_text 恰好20字 → needs_spec=False", t_spec_exactly20)

# ── 11. qty=0 的产品（其他产品有qty）────────────────────────────────────────
def t_qty_zero():
    params = {
        **SINGLE,
        "products": [
            {"name": "Zero Item", "sku": "Z-000", "qty": 0, "unit_price": 200},
            {"name": "Normal Item", "sku": "N-001", "qty": 3, "unit_price": 100},
        ],
        "lang": "cn", "format": "pdf",
    }
    r = generate_contract(params)
    d = _build_data(params)
    ok = (os.path.exists(r.get("cn_pdf", "")) and
          d["qty_total"] == 3 and
          d["goods_total"] == 300.0)
    return ok, f"qty_total={d['qty_total']}, goods_total={d['goods_total']}"

run("qty=0产品混合 → 文件生成且合计正确", t_qty_zero)

# ── 12. 空 products 列表 应报错 ──────────────────────────────────────────────
def t_empty_products():
    params = {**SINGLE, "products": [], "lang": "cn", "format": "pdf"}
    try:
        generate_contract(params)
        return False, "未抛出异常"
    except Exception as e:
        return True, f"正确抛出: {type(e).__name__}: {e}"

run("空 products 列表 → 应报错", t_empty_products)

# ── 13. products 中有空 name ──────────────────────────────────────────────────
def t_empty_name():
    params = {
        **SINGLE,
        "products": [{"name": "", "sku": "X-001", "qty": 1, "unit_price": 50}],
        "lang": "cn", "format": "pdf",
    }
    try:
        r = generate_contract(params)
        ok = os.path.exists(r.get("cn_pdf", ""))
        return ok, "空name仍生成文件（无崩溃）"
    except Exception as e:
        return False, f"EXCEPTION: {e}"

run("products 空 name → 不崩溃", t_empty_name)

# ── 14. 性能：生成4文件耗时 ───────────────────────────────────────────────────
def t_perf_4files():
    t0 = time.perf_counter()
    r = generate_contract({**MULTI, "lang": "both", "format": "both"})
    elapsed = time.perf_counter() - t0
    ok = len(r) == 4 and elapsed < 30
    return ok, f"耗时 {elapsed:.2f}s"

run("性能：多产品生成4文件 (<30s)", t_perf_4files)

# ── 汇总 ──────────────────────────────────────────────────────────────────────
total  = len(results)
passed = sum(1 for _, ok, _ in results if ok)
failed = total - passed
total_time = sum(t for _, _, t in results)

print()
print("=" * 60)
print(f"总计: {total} 项  通过: {passed}  失败: {failed}  总耗时: {total_time*1000:.0f}ms")
if failed:
    print("失败项:")
    for name, ok, _ in results:
        if not ok:
            print(f"  - {name}")
print("=" * 60)

sys.exit(0 if failed == 0 else 1)
