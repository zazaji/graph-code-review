#!/usr/bin/env python3
"""jscpd 报告摘要（零 LLM）。用法: python3 -I jscpd_digest.py <jscpd-report.json>

过滤 vendored/测试路径后按行数降序输出克隆对表；stdin 为空/文件缺失时输出说明。
"""
import json
import sys
from pathlib import Path

SKIP_HINTS = ("/tests/", "/test/", "test_", "_test.", "/static/lib/", ".min.js",
              "node_modules/", "site-packages/", "/build/", "/dist/", "/.venv/")


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python3 -I jscpd_digest.py <jscpd-report.json>", file=sys.stderr)
        return 2
    raw = Path(sys.argv[1])
    if not raw.is_file():
        print("(jscpd-report.json 不存在——检测未运行或目标为空)")
        return 0
    try:
        data = json.loads(raw.read_text(errors="replace"))
    except (json.JSONDecodeError, OSError):
        print("(报告解析失败)")
        return 0
    dups = data.get("duplicates") or []
    kept, skipped = [], 0
    seen = set()
    for x in dups:
        a, b = x.get("firstFile") or {}, x.get("secondFile") or {}
        fa, fb = a.get("name", ""), b.get("name", "")
        if fa > fb:
            fa, fb, sa, sb = fb, fa, b.get("start"), a.get("start")
        else:
            sa, sb = a.get("start"), b.get("start")
        if fa == fb and sa == sb:
            continue
        key = (fa, sa, fb, sb)
        if key in seen:
            continue
        seen.add(key)
        if any(h in fa or h in fb for h in SKIP_HINTS):
            skipped += 1
            continue
        kept.append((fa, sa, a.get("end"), fb, sb, b.get("end"), x.get("lines") or 0, x.get("kind", "?")))
    kept.sort(key=lambda t: -(t[6] or 0))
    print(f"# 重复克隆（jscpd，已过滤 vendored/tests）")
    print()
    print(f"克隆对 {len(dups)}；入表 {len(kept)}；剔除（vendor/tests/重复）{skipped + len(dups) - len(kept) - len([1 for x in dups if x.get('firstFile') == x.get('secondFile')])}。")
    print()
    print("| 文件A | 行 | 文件B | 行 | 行数 |")
    print("|---|---|---|---|---|")
    for fa, sa, ea, fb, sb, eb, ln, kind in kept[:25]:
        print(f"| {fa}:{sa}-{ea} |  | {fb}:{sb}-{eb} |  | {ln} |")
    if not kept:
        print("(无入表克隆对)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
