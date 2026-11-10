#!/usr/bin/env python3
"""跨文件调用图生成（零 LLM）。用法: python3 -I callgraph.py <calls_raw.txt> <outdir> [项目名]

输入: CBM query_graph 'MATCH (a:Function)-[:CALLS]->(b:Function) RETURN a.name,a.file,b.name,b.file' 的 tree 输出
输出: graph.html（自包含交互力导向图，模板 scripts/graph_template.html）、graph.mmd（Mermaid 文件级）、graph.dot（DOT 函数级）
规则: 只画跨文件调用（a.file <> b.file），同文件内部调用不上图；vendor/tests/非代码文件剔除。
"""
import hashlib
import json
import sys
from pathlib import Path

MAX_NODES = 350          # 函数节点超过则降级为文件级图
TOP_FILES = 40           # 文件级图/Mermaid 最多节点
NODE_COLORS = ["#33ff99", "#5fa8ff", "#ff9933", "#ff5f8f", "#33e0ff", "#c9ff33",
               "#b033ff", "#ffcb5f", "#5fd4ff", "#ff8f5f", "#8fff5f", "#ff5fd2",
               "#5fff9e", "#ffd433", "#a833ff", "#33ffd4", "#ff6b5f", "#9effc2"]
SKIP_HINTS = ("/tests/", "/test/", "test_", "_test.", "/static/lib/", ".min.js",
              "node_modules/", "site-packages/", "/build/", "/dist/", "/.venv/")
CODE_SUFFIX = (".py", ".js", ".ts", ".jsx", ".tsx", ".vue", ".go")


def expand_fileref(fileref: str, refs: dict):
    if not fileref.startswith("@"):
        return fileref
    body = fileref[1:]
    if "+" not in body:
        return None
    n, suffix = body.split("+", 1)
    return refs.get(n, "") + suffix if n.isdigit() else None


def parse_calls(text: str):
    """两态扫描 rows_refs/@N+ 压缩。返回 [(a_name,a_file,b_name,b_file)]。"""
    refs, edges, sec = {}, [], None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("rows_refs:"):
            sec = "refs"; continue
        if s.startswith(("rows_ref_rule:", "rows:")):
            sec = "rows"; continue
        if not s or not line.startswith("  "):
            continue
        if s.startswith(("returned:", "total", "has_more", "truncated", "cols", "(")):
            continue
        p = s.split()
        if sec == "refs" and len(p) >= 2 and p[0].isdigit():
            refs[p[0]] = p[1]
        elif sec == "refs":
            continue
        elif len(p) >= 4:
            fa, fb = expand_fileref(p[1], refs), expand_fileref(p[3], refs)
            if fa and fb:
                edges.append((p[0], fa, p[2], fb))
    return edges


def file_color(f: str) -> str:
    return NODE_COLORS[int(hashlib.md5(f.encode()).hexdigest(), 16) % len(NODE_COLORS)]


def filter_edges(raw_edges):
    """剔除 vendor/tests/非代码/同文件调用。返回 (func_edges, dropped)。"""
    fun, seen, dropped = [], set(), 0
    for a, fa, b, fb in raw_edges:
        if Path(fa).suffix not in CODE_SUFFIX or Path(fb).suffix not in CODE_SUFFIX:
            dropped += 1
            continue
        if any(h in fa or h in fb for h in SKIP_HINTS):
            dropped += 1
            continue
        if fa == fb:                       # 同文件内部调用——不上图
            continue
        if (a, fa, b, fb) in seen:
            continue
        seen.add((a, fa, b, fb))
        fun.append((a, fa, b, fb))
    return fun, dropped


def file_graph(fun):
    """文件级聚合图。"""
    fdeg, fagg = {}, {}
    for a, fa, b, fb in fun:
        fdeg[fa] = fdeg.get(fa, 0) + 1
        fdeg[fb] = fdeg.get(fb, 0) + 1
        fagg[(fa, fb)] = fagg.get((fa, fb), 0) + 1
    top = sorted(fdeg, key=lambda f: (-fdeg[f], f))[:TOP_FILES]
    ix = {f: i for i, f in enumerate(top)}
    nodes = [{"id": f, "label": Path(f).name, "file": f, "color": file_color(f), "deg": fdeg[f]}
             for f in top]
    edges = [[ix[fa], ix[fb], w] for (fa, fb), w in sorted(fagg.items(), key=lambda t: (-t[1], t[0]))
             if fa in ix and fb in ix]
    return nodes, edges, "file"


def func_graph(fun):
    """函数级图。"""
    deg, agg = {}, {}
    for a, fa, b, fb in fun:
        for k in ((a, fa), (b, fb)):
            deg[k] = deg.get(k, 0) + 1
        key = ((a, fa), (b, fb))
        agg[key] = agg.get(key, 0) + 1
    keys = sorted(deg, key=lambda k: (-deg[k], k))
    ix = {k: i for i, k in enumerate(keys)}
    nodes = [{"id": k[0] + "@" + k[1], "label": k[0], "file": k[1], "color": file_color(k[1]),
              "deg": deg[k]} for k in keys]
    edges = [[ix[(a, fa)], ix[(b, fb)], agg[((a, fa), (b, fb))]]
             for (a, fa, b, fb) in fun if (a, fa) in ix and (b, fb) in ix]
    return nodes, edges, "function"


def mermaid(fun):
    """文件级 flowchart（跨文件对账总览，带调用次数）。"""
    fdeg, fagg = {}, {}
    for a, fa, b, fb in fun:
        fdeg[fa] = fdeg.get(fa, 0) + 1
        fdeg[fb] = fdeg.get(fb, 0) + 1
        fagg[(fa, fb)] = fagg.get((fa, fb), 0) + 1
    top = sorted(fdeg, key=lambda f: (-fdeg[f], f))[:TOP_FILES]
    ix = {f: i for i, f in enumerate(top)}
    lines = ["flowchart LR"]
    for f in top:
        lines.append(f'  f{ix[f]}["{Path(f).name}"]')
    for (fa, fb), w in sorted(fagg.items(), key=lambda t: (-t[1], t[0])):
        if fa in ix and fb in ix:
            lines.append(f'  f{ix[fa]} -- "{w}" --> f{ix[fb]}')
    if len(fdeg) > TOP_FILES:
        lines.append(f"  %% 注：涉及文件 {len(fdeg)} 个，仅显示度数最高的 {TOP_FILES} 个")
    return "\n".join(lines) + "\n"


def dot(nodes, edges):
    """函数级 DOT，按文件 cluster。"""
    lines = ["digraph calls {", "  rankdir=LR;",
             '  node [shape=box, style=filled, fontsize=10, fontcolor="#dfe9e2", color="#2a4a3a"];']
    byfile = {}
    for i, n in enumerate(nodes):
        byfile.setdefault(n["file"], []).append(i)
    for ci, (f, mem) in enumerate(sorted(byfile.items())):
        lines.append(f'  subgraph cluster_{ci} {{')
        lines.append(f'    label="{f}";')
        for i in mem:
            n = nodes[i]
            label = n["label"].replace('"', '\\"')
            lines.append(f'    n{i} [label="{label}", fillcolor="{n["color"]}"];')
        lines.append("  }")
    for s, t, w in edges:
        lines.append(f'  n{s} -> n{t} [label="{w}"];')
    lines.append("}")
    return "\n".join(lines) + "\n"


def main() -> int:
    if len(sys.argv) < 3:
        print("用法: python3 -I callgraph.py <calls_raw.txt> <outdir> [项目名]", file=sys.stderr)
        return 2
    raw, outdir = Path(sys.argv[1]), Path(sys.argv[2])
    name = sys.argv[3] if len(sys.argv) > 3 else ""
    if not raw.is_file():
        print("(calls_raw.txt 不存在——CALLS 查询未运行)")
        return 0
    raw_edges = parse_calls(raw.read_text(errors="replace"))
    fun, dropped = filter_edges(raw_edges)

    if not fun:
        (outdir / "graph.mmd").write_text("flowchart LR\n  empty[\"无跨文件调用\"]\n")
        (outdir / "graph.dot").write_text("digraph calls { }\n")
        (outdir / "graph.html").write_text(
            Path(__file__).parent.joinpath("graph_template.html").read_text()
            .replace("__TITLE__", name).replace("__STATS__", "无跨文件调用")
            .replace("__LEGEND__", "").replace("__DATA__", '{"nodes":[],"edges":[]}'))
        print(f"graph: 无跨文件调用（原始 CALLS 边 {len(raw_edges)}，全部为同文件/vendor/非代码，剔除 {dropped}）")
        return 0

    deg = {}
    for a, fa, b, fb in fun:
        for k in ((a, fa), (b, fb)):
            deg[k] = deg.get(k, 0) + 1
    if len(deg) <= MAX_NODES:
        nodes, edges, mode = func_graph(fun)
    else:
        nodes, edges, mode = file_graph(fun)

    fdeg = {}
    for a, fa, b, fb in fun:
        fdeg[fa] = fdeg.get(fa, 0) + 1
        fdeg[fb] = fdeg.get(fb, 0) + 1
    legend_files = sorted(fdeg, key=lambda f: -fdeg[f])[:12]
    legend = "<br>".join(
        f'<span class="sw" style="background:{file_color(f)}"></span>{Path(f).name} ({fdeg[f]})'
        for f in legend_files)

    stats = (f"项目 {name} ｜ {mode} 模式 ｜ 节点 {len(nodes)} · 边 {len(edges)} ｜ "
             f"跨文件调用 {len(fun)}（同文件/vendor/非代码剔除 {dropped} 条）｜ 拖动节点 · 悬停高亮")
    tmpl = Path(__file__).parent / "graph_template.html"
    html = (tmpl.read_text()
            .replace("__TITLE__", name)
            .replace("__STATS__", stats)
            .replace("__LEGEND__", legend)
            .replace("__DATA__", json.dumps({"nodes": nodes, "edges": edges}, ensure_ascii=False)))

    (outdir / "graph.html").write_text(html)
    (outdir / "graph.mmd").write_text(mermaid(fun))
    (outdir / "graph.dot").write_text(dot(nodes, edges))
    print(f"graph: {len(nodes)} 节点 / {len(edges)} 边 (mode={mode}) → graph.html / graph.mmd / graph.dot")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
