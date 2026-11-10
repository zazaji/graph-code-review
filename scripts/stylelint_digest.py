#!/usr/bin/env python3
"""CSS 审计摘要（零 LLM）。用法: python3 -I stylelint_digest.py <stylelint.json> > stylelint.md

输入: stylelint -f json 的输出数组；vendor 路径剔除；按文件聚合违规。
"""
import json
import sys
from pathlib import Path

SKIP = ("/static/lib/", ".min.css", "node_modules/", "site-packages/", "/.venv/", "/build/", "/dist/")


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python3 -I stylelint_digest.py <stylelint.json>", file=sys.stderr)
        return 2
    p = Path(sys.argv[1])
    print("# CSS 审计（stylelint，核心规则集）")
    print()
    if not p.is_file() or not p.read_text(errors="replace").strip():
        print("（未运行或目标无 CSS 文件——仅 .css/.scss 参与审计）")
        return 0
    try:
        d = json.loads(p.read_text(errors="replace"))
    except (json.JSONDecodeError, OSError):
        print("（stylelint.json 解析失败）")
        return 0
    total = 0
    rows = []
    for f in d:
        src = (f.get("source") or "?").replace("file://", "")
        ws = f.get("warnings") or []
        if any(h in src for h in SKIP):
            continue
        if ws:
            rows.append((src.rsplit("/", 1)[-1], ws))
            total += len(ws)
    if not total:
        print("（0 违规——CSS 干净；本阶段为回归守卫）")
        return 0
    print(f"违规 {total} 处，涉及 {len(rows)} 个文件。\n")
    print("| 文件 | 行 | 规则 | 问题 |")
    print("|---|---|---|---|")
    shown = 0
    for src, ws in rows:
        for w in ws[:12]:
            print(f"| {src} | {w.get('line','?')} | {w.get('rule','?')} | {(w.get('text','') or '')[:55]} |")
            shown += 1
            if shown >= 40:
                break
        if shown >= 40:
            print(f"\n（其余见 stylelint.json）")
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
