#!/usr/bin/env python3
"""守卫/权限面汇总（零 LLM）。用法: python3 -I authz_digest.py <zift.json> [febe.txt] > authz.md

输入: zift scan -f json 的输出（嵌入式鉴权点清单）+ 可选 febe 审计的 febe.txt（路由总数）
输出: authz.md——守卫面分布、top 命中、路由×守卫覆盖提示
"""
import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python3 -I authz_digest.py <zift.json> [febe.txt]", file=sys.stderr)
        return 2
    zift_path = Path(sys.argv[1])
    target = sys.argv[3] if len(sys.argv) > 3 else ""

    def rel(p):
        return p[len(target):].lstrip("/") if target and p.startswith(target) else p
    print("# 守卫/权限面清单（zift，嵌入式鉴权点）")
    print()
    if not zift_path.is_file():
        print("（zift 未运行或未安装——安装: 见 install.sh 步骤 [5/6]）")
        return 0
    try:
        d = json.loads(zift_path.read_text(errors="replace"))
    except (json.JSONDecodeError, OSError):
        print("（zift.json 解析失败）")
        return 0
    fs = d.get("findings", [])
    hl = d.get("headline", {})
    print(f"嵌入式鉴权点 {hl.get('embedded_findings', len(fs))} 个"
          f"（外部化策略 {hl.get('externalized', 0)}，外部化率 {hl.get('externalized_pct', 0)}%）。")
    print()
    if not fs:
        print("**零命中**——若目标确有鉴权，说明鉴权走外部网关/平台层；若目标应有鉴权而清单为空，")
        print("即存在系统性缺失（如管理面裸奔），结合 security.md 与 febe.txt 路由表定案。")
        return 0
    from collections import Counter
    cat = Counter(f.get("category", "?") for f in fs)
    surf = Counter(f.get("surface", "?") for f in fs)
    print(f"类别: {', '.join(f'{k} {v}' for k, v in cat.most_common())}")
    print(f"面: {', '.join(f'{k} {v}' for k, v in surf.most_common())}")
    print()
    print("| 位置 | 类别 | 置信 | 说明 |")
    print("|---|---|---|---|")
    conf_rank = {"high": 0, "medium": 1, "low": 2}
    for f in sorted(fs, key=lambda x: conf_rank.get(x.get("confidence", "low"), 3))[:30]:
        print(f"| {rel(f.get('file','?'))}:{f.get('line_start','?')} "
              f"| {f.get('category','?')} | {f.get('confidence','?')} "
              f"| {(f.get('description','') or '')[:60]} |")
    if len(fs) > 30:
        print(f"\n（其余 {len(fs)-30} 条见 zift.json）")
    if len(sys.argv) > 2 and Path(sys.argv[2]).is_file():
        txt = Path(sys.argv[2]).read_text(errors="replace")
        for line in txt.splitlines():
            if line.startswith("# FE/BE") or "后端路由" in line:
                print(f"\n路由对照（febe）: {line.strip()}")
                break
        print("交叉检查提示: 路由总数明显高于鉴权点数时，存在无守卫端面（AGHAST '缺守卫' 检查可精确定位，见 SKILL.md）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
