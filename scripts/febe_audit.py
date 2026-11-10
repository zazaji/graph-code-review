#!/usr/bin/env python3
"""FE/BE 接口对账（零 LLM）。用法: python3 -I febe_audit.py <目标绝对路径>

传统方法：regex 提取后端路由装饰器（FastAPI/Flask）+ 前端请求 URL，规范化后集合差。
- 后端路由无前端调用 → 疑似死接口
- 前端调用无后端路由 → 潜在不一致（动态拼接/打错路径，需人工判断）
路径参数统一规范化为 :p（{id} / ${id} / <int:id> / :id）后按整路径匹配。
"""
import os
import re
import sys
from pathlib import Path

SKIP_DIRS = {".git", ".gitnexus", ".code-review-graph", ".codegraph", "node_modules",
             "__pycache__", "build", "dist", ".venv", "venv", "cbm-cache", "cbm-review"}
BACKEND_RE = re.compile(
    r"""@[\w\.]+\.(get|post|put|patch|delete|route|websocket)\(\s*['"]([^'"]+)['"]"""
)
FE_RES = (
    re.compile(r"""fetch\(\s*[`'"]([^`'"]+)"""),
    re.compile(r"""axios\.\w+\(\s*[`'"]([^`'"]+)['"`]"""),
    re.compile(r"""url:\s*[`'"]([^'"`]+)['"`]"""),
)
FE_SUFFIX = {".js", ".ts", ".jsx", ".tsx", ".vue"}


def norm_path(raw: str) -> str:
    s = raw.strip().strip("`'\"")
    if "://" in s:
        rest = s.split("://", 1)[1]
        s = "/" + rest.split("/", 1)[1] if "/" in rest else "/"
    s = s.split("?")[0].split("#")[0]
    s = re.sub(r"\$\{[^}]*\}", ":p", s)           # JS 模板串
    s = re.sub(r"\{[^}]*\}", ":p", s)             # {id}
    s = re.sub(r":\w+", ":p", s)                  # :id
    s = re.sub(r"<(?:[^<>]*:)?[^<>]+>", ":p", s)  # Flask <int:id>
    return "/" + s.strip("/")


def is_vendor(path_str: str) -> bool:
    return any(h in path_str for h in ("node_modules", ".min.js", "/static/lib/", ".venv"))


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python3 -I febe_audit.py <目标绝对路径>", file=sys.stderr)
        return 2
    root = Path(sys.argv[1]).resolve()
    backend, frontend = [], []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            sp = os.path.join(dirpath, fn)
            suffix = Path(fn).suffix
            if suffix not in ({".py"} | FE_SUFFIX):
                continue
            if ".min.js" in fn or "/static/lib/" in sp:
                continue
            try:
                text = Path(os.path.join(dirpath, fn)).read_text(errors="replace")
            except OSError:
                continue
            if suffix == ".py":
                for i, line in enumerate(text.splitlines(), 1):
                    m = BACKEND_RE.search(line)
                    if m:
                        backend.append((sp, i, m.group(1).upper(), norm_path(m.group(2))))
            elif suffix in FE_SUFFIX:
                for i, line in enumerate(text.splitlines(), 1):
                    for pat in FE_RES:
                        for m in pat.finditer(line):
                            frontend.append((sp, i, norm_path(m.group(1))))
    backend_paths = {}
    for f, ln, verb, p in backend:
        backend_paths.setdefault(p, []).append((f, ln, verb))
    fe_paths = {}
    for f, ln, p in frontend:
        fe_paths.setdefault(p, []).append((f, ln))
    matched = sorted(set(backend_paths) & set(fe_paths))
    backend_only = [p for p in sorted(backend_paths) if p not in fe_paths]
    fe_only = [p for p in sorted(fe_paths) if p not in backend_paths]

    print("# FE/BE 接口对账")
    print()
    print(f"后端路由 {len(backend_paths)} 个整路径；前端请求 {len(fe_paths)} 个整路径；"
          f"匹配 {len(matched)}；后端无前端调用 {len(backend_only)}（疑似死接口）；前端无路由 {len(fe_only)} 个。")
    print()
    print("## 疑似死接口（后端路由无前端调用）")
    print()
    print("| 路由 | 后端位置 |")
    print("|---|---|")
    for p in backend_only:
        first = backend_paths[p][0]
        print(f"| {p} | {first[0]}:{first[1]} ({first[2]}) |")
    print()
    print("## 前端调用无路由（需人工定案：动态拼接或路径不一致）")
    print()
    print("| 前端调用 | 请求位置 |")
    print("|---|---|")
    for p in fe_only:
        for f, ln in fe_paths[p][:2]:
            print(f"| {p} | {f}:{ln} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
