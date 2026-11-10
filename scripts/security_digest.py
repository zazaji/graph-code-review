#!/usr/bin/env python3
"""安全扫描汇总（零 LLM）。用法:
python3 -I security_digest.py <bandit.json|-> <leaks.json|-> <osv.json|-> <og_auto.json|-> <og_custom.json|-> > security.md

四源各自容错：缺失/空文件/JSON 解析失败都输出占位说明。vendor/tests 路径统一剔除。
"""
import json
import sys
from pathlib import Path

SKIP = ("/tests/", "/test/", "test_", "_test.", "/static/lib/", ".min.js",
        "node_modules/", "site-packages/", "/build/", "/dist/", "/.venv/")


def load(path):
    if not path or path == "-" or not Path(path).is_file():
        return None
    try:
        return json.loads(Path(path).read_text(errors="replace"))
    except (json.JSONDecodeError, OSError):
        return None


def dirty(p: str) -> bool:
    return any(h in p for h in SKIP)


def sec(title):
    print(f"\n## {title}\n")


def table(headers, rows, empty_msg):
    if not rows:
        print(f"（{empty_msg}）")
        return
    print("| " + " | ".join(headers) + " |")
    print("|" + "|".join("---" for _ in headers) + "|")
    for r in rows[:40]:
        print("| " + " | ".join(str(x).replace("|", "\\|") for x in r) + " |")
    if len(rows) > 40:
        print(f"\n（其余 {len(rows)-40} 条见原始 JSON）")


def main() -> int:
    bandit = load(sys.argv[1]) if len(sys.argv) > 1 else None
    leaks = load(sys.argv[2]) if len(sys.argv) > 2 else None
    osv = load(sys.argv[3]) if len(sys.argv) > 3 else None
    og_auto = load(sys.argv[4]) if len(sys.argv) > 4 else None
    og_custom = load(sys.argv[5]) if len(sys.argv) > 5 else None
    target = sys.argv[6] if len(sys.argv) > 6 else ""

    def rel(p):
        p = (p or "").replace("file://", "")
        return p[len(target):].lstrip("/") if target and p.startswith(target) else p

    print("# 安全扫描汇总（bandit · betterleaks · osv-scanner · opengrep）")
    print()
    print("工具均为可选阶段——未安装自动跳过，对应小节显示占位。")

    sec("依赖漏洞（osv-scanner，锁文件 vs OSV 数据库）")
    if osv is None:
        print("（未运行或无锁文件可扫）")
    else:
        rows = []
        for res in osv.get("results", []):
            src = res.get("source", {}).get("path", "?")
            for pkg in res.get("packages", []):
                name = pkg.get("package", {}).get("name", "?")
                ver = pkg.get("package", {}).get("version", "?")
                for v in pkg.get("vulnerabilities", []):
                    fix = ""
                    for a in v.get("affected", []):
                        if a.get("package", {}).get("name") == name:
                            ranges = a.get("ranges", [])
                            for rg in ranges:
                                for ev in rg.get("events", []):
                                    if "fixed" in ev:
                                        fix = ev["fixed"]
                    aliases = ",".join(v.get("aliases", [])[:2])
                    rows.append((v.get("id", "?"), aliases, name, ver, fix, src.rsplit("/", 1)[-1]))
        print(f"漏洞 {len(rows)} 条。\n")
        table("CVE/ID | 别名 | 包 | 版本 | 修复版本 | 清单", rows, "无已知漏洞")

    sec("密钥泄露（betterleaks，代码+备份+压缩包内）")
    if leaks is None:
        print("（未运行）")
    else:
        rows = []
        for x in leaks:
            f = x.get("File", "")
            rows.append((x.get("RuleID", "?"), f.rsplit("/", 1)[-1], x.get("StartLine", "?"),
                         (x.get("Secret", "") or "")[:6] + "…"))
        print(f"发现 {len(rows)} 处（含备份/压缩包内命中——均需人工确认真伪后轮换）。\n")
        table("规则 | 文件 | 行 | 密钥前缀", rows, "未发现泄露")

    sec("代码安全（bandit，Python AST 级）")
    if bandit is None:
        print("（未运行）")
    else:
        rs = [r for r in bandit.get("results", []) if not dirty(r.get("filename", ""))]
        sev_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        rs.sort(key=lambda r: sev_order.get(r.get("issue_severity"), 3))
        rows = [(r.get("issue_severity", "?"), r.get("test_id", "?"),
                 rel(r.get("filename", "?")) + ":" + str(r.get("line_number", "?")),
                 (r.get("issue_text", "") or "")[:60]) for r in rs]
        print(f"发现 {len(rs)} 条（HIGH 优先）。\n")
        table("严重度 | 规则 | 位置 | 问题", rows, "未发现")

    sec("模式扫描（opengrep，auto 注册表规则 + 自定义数字格式规则）")
    rows = []
    if og_auto:
        rs = [r for r in og_auto.get("results", []) if not dirty(r.get("path", ""))]
        for r in rs:
            rows.append(("auto", r.get("check_id", "?").split(".")[-2] if "." in r.get("check_id", "?") else r.get("check_id", "?"),
                         rel(r.get("path", "?")) + ":" + str(r.get("start", {}).get("line", "?")),
                         (r.get("extra", {}).get("message", "") or "")[:55]))
    if og_custom:
        rs = [r for r in og_custom.get("results", []) if not dirty(r.get("path", ""))]
        for r in rs:
            rows.append(("numeric", r.get("check_id", "?").split(".")[-1],
                         rel(r.get("path", "?")) + ":" + str(r.get("start", {}).get("line", "?")),
                         (r.get("extra", {}).get("message", "") or "")[:55]))
    if og_auto is None:
        print("auto：未运行（网络不可达或超时）")
    if og_custom is None:
        print("numeric：未运行")
    if rows:
        print(f"\n命中 {len(rows)} 条（vendor 噪音已剔除）。\n")
        table("来源 | 规则 | 位置 | 消息", rows, "未命中")
    elif og_auto and og_custom:
        print("\n（两套规则均零命中）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
