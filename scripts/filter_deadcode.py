#!/usr/bin/env python3
"""死代码候选过滤（零 LLM）。用法: python3 -I filter_deadcode.py <deadcode_raw.txt> <目标绝对路径>

解析 CBM query_graph 的 tree 输出（含大结果集的 rows_refs/@N+ 路径压缩），
对每个候选用 rg 全仓验证：除定义行自身外零命中才保留（保守剔除，宁缺毋滥）。
实测背景：CALLS 图不识别 FastAPI/Flask 装饰器注册的
handler、self. 方法调用、嵌套闭包——这些误报由"有调用命中"规则自动剔除。
"""
import re
import subprocess
import sys
from pathlib import Path

SKIP_PATH_HINTS = ("/tests/", "test_", "_test.", "/static/lib/", ".min.js",
                   "node_modules/", "/build/", "/dist/", "/.venv/")
CODE_SUFFIX = {".py", ".js", ".ts", ".jsx", ".tsx", ".vue", ".go"}


def expand_fileref(fileref: str, refs: dict):
    """'@8+app.js' -> refs['8']+'app.js'；非 @ 引用原样返回。"""
    if not fileref.startswith("@"):
        return fileref
    body = fileref[1:]
    if "+" not in body:
        return None
    n_str, suffix = body.split("+", 1)
    if not n_str.isdigit():
        return None
    return refs.get(int(n_str), "") + suffix


def parse_tree(text: str):
    """解析 CBM tree 输出（含 rows_refs/@N+ 压缩）。返回 [(name, path, lineno_str)]。"""
    refs = {}
    rows = []
    section = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("rows_refs:"):
            section = "refs"
            continue
        if stripped.startswith("rows_ref_rule:") or stripped.startswith("rows:"):
            section = "rows"
            continue
        if stripped.startswith(("returned:", "total:", "total_relation", "has_more",
                                "truncated", "cols", "(cols")):
            continue
        if not stripped or not line.startswith("  "):
            continue
        parts = stripped.split()
        if section == "refs":
            if len(parts) >= 2 and parts[0].isdigit():
                refs[parts[0]] = parts[1]
            continue
        if len(parts) < 3:
            continue
        name, fileref, lineno = parts[0], parts[1], parts[2]
        path = expand_fileref(fileref, refs)
        if Path(path).suffix not in CODE_SUFFIX:
            continue
        if any(h in path for h in SKIP_PATH_HINTS):
            continue
        rows.append((name, path, lineno))
    return rows


def symbol_hits(name: str, target: str):
    """rg 全仓符号命中 -> [(file, line, text)]。pattern 经 -e 显式传参，规避 rg 旗标注入。"""
    proc = subprocess.run(
        ["rg", "--no-heading", "-n", "-e", rf"\b{re.escape(name)}\b", "--", target],
        capture_output=True, text=True, timeout=120,
    )
    hits = []
    for line in (proc.stdout or "").splitlines():
        head = line.split(":", 2)
        if len(head) >= 2 and head[1].isdigit():
            hits.append((head[0], int(head[1]), head[2] if len(head) > 2 else ""))
    return hits


def to_lineno(s: str) -> int:
    try:
        return int(s)
    except ValueError:
        return -1


def main() -> int:
    if len(sys.argv) < 3:
        print("用法: python3 -I filter_deadcode.py <deadcode_raw.txt> <目标绝对路径>", file=sys.stderr)
        return 2
    raw_path, target = sys.argv[1], sys.argv[2]
    candidates = parse_tree(Path(raw_path).read_text(errors="replace"))
    kept, dropped = [], 0
    for name, fname, lineno in candidates:
        try:
            hits = symbol_hits(name, target)
        except Exception:
            continue
        defline = int(lineno) if lineno.isdigit() else -1
        def_re = re.compile(
            rf"^\s*(?:async\s+)?def\s+{re.escape(name)}\s*\(|"
            rf"function\s+{re.escape(name)}\s*\(|"
            rf"(?:const|let|var)\s+{re.escape(name)}\s*="
        )
        has_caller = False
        for hf, hl, htext in hits:
            if hf == fname and hl == defline:
                continue
            if def_re.match(htext):
                continue
            has_caller = True
            break
        if has_caller:
            dropped += 1
        else:
            kept.append((name, fname, lineno))
    print("# 死代码候选（已过滤误报）")
    print()
    print(f"原始候选 {len(candidates)} 条；rg 全仓验证剔除 {dropped} 条（有调用命中），保留 {len(kept)} 条零调用候选。")
    print()
    print("零调用验证通过的候选（仍需人工确认语义，如 MCP tool/CLI 注册场景）：")
    print()
    print("| 符号 | 位置 |")
    print("|---|---|")
    for name, fname, lineno in kept:
        print(f"| {name} | {fname}:{lineno} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
