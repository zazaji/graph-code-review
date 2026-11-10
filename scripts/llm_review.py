#!/usr/bin/env python3
"""LLM 审核阶段（可选模式——需配置 LLM）。用法:
  python3 -I llm_review.py <project_path> <outdir> [--config <llm.json>] [--no-verify] [--max-hotspots 6]

流程（复刻 Anthropic 代码审核实践 + 跨模型研究结论）:
  确定性证据(outdir) → 热点摘录(±40行, 有界预算) → review 模型出 findings(JSON)
  → [可选] verify 模型逐条核验(跨家族更佳) → 丢弃假阳性 → llm_review.md
证据解析/热点摘录零 LLM；LLM 只读热点；每条 finding 必须带 file:line 证据。
已知坑（MyKG）: glm 系输出 JSON 夹带控制/零宽字符——清洗后解析，失败重试一次，仍败则降级存 raw。
"""
import json
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_CONFIG = {
    "backend": "opencode",        # opencode | claude | ollama | openai
    "review_model": "",           # 空=后端默认模型
    "verify_model": "",           # 空=不核验；建议跨家族（如 reviewer=glm 则 verifier=claude 系）
    "base_url": "",               # backend=openai 时必填（OpenAI 兼容 /chat/completions）
    "api_key_env": "LLM_API_KEY", # backend=openai 时读取的环境变量名
    "max_hotspots": 6,            # 单次审核热点组数上限
    "budget_chars": 60000         # 摘录总字符预算（≈15k tokens）
}
LINE_CTX = 40
EVIDENCE_SOURCES = ["security.md", "deadcode_verified.md", "authz.md", "stylelint.md", "danger.txt", "similar.txt"]


def load_config(path):
    cfg = dict(DEFAULT_CONFIG)
    if path and Path(path).is_file():
        try:
            cfg.update(json.loads(Path(path).read_text()))
        except (json.JSONDecodeError, OSError):
            pass
    elif (Path.home() / ".config/graph-code-review/llm.json").is_file():
        try:
            cfg.update(json.loads((Path.home() / ".config/graph-code-review/llm.json").read_text()))
        except (json.JSONDecodeError, OSError):
            pass
    return cfg


def _scan_from(t, pos, opener):
    """从 pos 起独立做括号配平扫描（字符串态隔离），返回平衡块或 None。"""
    closer = "]" if opener == "[" else "}"
    stack, in_str, esc, j = [], False, False, pos
    while j < len(t):
        ch = t[j]
        if in_str:
            if esc: esc = False
            elif ch == "\\": esc = True
            elif ch == '"': in_str = False
        else:
            if ch == '"': in_str = True
            elif ch == opener: stack.append(1)
            elif ch == closer:
                stack.pop()
                if not stack: return t[pos:j+1]
        j += 1
    return None


def clean_llm_json(text, expect="list"):
    """容错解析：控制/零宽字符清洗（MyKG 坑）+ 尾逗号宽容 + 每个 opener 位置独立起扫
    （防叙述文本的未配对引号把扫描器毒化在字符串态——实测坑）。"""
    if not text or not isinstance(text, str):
        return None
    t = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\u200b-\u200f\u2060\ufeff]", "", text)
    def loads_lenient(c):
        for c2 in (c, re.sub(r",\s*([}\]])", r"\1", c)):
            try:
                return json.loads(c2)
            except (json.JSONDecodeError, ValueError):
                continue
        return None
    openers = ["[", "{"] if expect == "list" else ["{", "["]
    for opener in openers:
        pos = t.find(opener)
        while pos != -1:
            cand = _scan_from(t, pos, opener)
            if cand:
                v = loads_lenient(cand)
                if v is not None:
                    if not isinstance(v, list) or not v or all(isinstance(x, dict) for x in v):
                        return v
            pos = t.find(opener, pos + 1)
    return None


def parse_evidence(outdir):
    """从确定性产物抽取 (source, file, line, desc)；md 表格行优先。"""
    items, seen = [], set()
    for name in EVIDENCE_SOURCES:
        p = Path(outdir) / name
        if not p.is_file():
            continue
        for ln in p.read_text(errors="replace").splitlines():
            if not ln.strip().startswith("|") or "---" in ln or ln.startswith("| 文件") or ln.startswith("| 符号") \
               or ln.startswith("| 路由") or ln.startswith("| 规则") or ln.startswith("| 严重度") or ln.startswith("| 位置") \
               or ln.startswith("| 来源") or ln.startswith("| CVE") or ln.startswith("| 文件A"):
                continue
            cells = [c.strip() for c in ln.strip("|").split("|")]
            loc = next((c for c in cells if re.search(r":\d+", c)), "")
            desc = next((c for c in cells if len(c) > 12 and ":" not in c[:1]), cells[-1] if cells else "")
            if not loc:
                continue
            m = re.search(r"([\w./+-]+\.(?:py|js|ts|css|go)):(\d+)", loc)
            if not m:
                continue
            key = (m.group(1), m.group(2))
            if key in seen:
                continue
            seen.add(key)
            items.append({"source": name, "file": m.group(1), "line": int(m.group(2)),
                          "desc": f"[{name}] {desc[:90]}"})
    return items


def build_hotspots(items, project, budget):
    """按文件聚合证据 → ±40 行摘录；REVIEW.md/前端文件注入对应 rubric 提示。"""
    byfile = {}
    for it in items:
        byfile.setdefault(it["file"], []).append(it)
    hotspots, used = [], 0
    for f, its in sorted(byfile.items(), key=lambda t: -len(t[1])):
        fp = Path(project) / f
        if not fp.is_file():
            continue
        try:
            lines = fp.read_text(errors="replace").splitlines()
        except OSError:
            continue
        l0 = max(1, its[0]["line"] - LINE_CTX)
        l1 = min(len(lines), its[-1]["line"] + LINE_CTX)
        excerpt = "\n".join(f"{n:5} {lines[n-1]}" for n in range(l0, l1 + 1))
        block = f"### 热点: {f}（证据 {len(its)} 条）\n" + "\n".join(f"- {i['desc']} @{i['line']}" for i in its) \
                + f"\n```（{f}:{l0}-{l1}）\n{excerpt}\n```"
        if used + len(block) > budget:
            break
        used += len(block)
        hotspots.append(block)
    return hotspots


def llm_call(cfg, prompt):
    """按 backend 调 LLM，返回文本或 None。"""
    try:
        if cfg["backend"] == "opencode":
            args = ["opencode", "run"]
            if cfg.get("review_model"):
                args += ["-m", cfg["review_model"]]
            args.append(prompt)
            r = subprocess.run(args, capture_output=True, text=True, timeout=600)
        elif cfg["backend"] == "claude":
            args = ["claude", "-p", prompt]
            if cfg.get("review_model"):
                args = ["claude", "-p", "--model", cfg["review_model"], prompt]
            r = subprocess.run(args, capture_output=True, text=True, timeout=600)
        elif cfg["backend"] == "ollama":
            args = ["ollama", "run", cfg["review_model"], prompt]
            r = subprocess.run(args, capture_output=True, text=True, timeout=600)
        elif cfg["backend"] == "openai":
            import os
            import urllib.request
            key = os.environ.get(cfg.get("api_key_env", "LLM_API_KEY"), "")
            body = json.dumps({"model": cfg["review_model"],
                               "messages": [{"role": "user", "content": prompt}]}).encode()
            req = urllib.request.Request(
                cfg["base_url"].rstrip("/") + "/chat/completions", data=body,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
            with urllib.request.urlopen(req, timeout=600) as resp:
                r = type("R", (), {"stdout": json.loads(resp.read())["choices"][0]["message"]["content"],
                                   "returncode": 0})()
        else:
            return None
        out = (r.stdout or "").strip()
        return out if out and "BACKEND" not in out[:10] else (out or None)
    except (subprocess.TimeoutExpired, OSError, KeyError, IndexError) as e:
        print(f"  LLM 调用失败: {e}", file=sys.stderr)
        return None


def main() -> int:
    if len(sys.argv) < 3:
        print("用法: python3 -I llm_review.py <project_path> <outdir> [--config f] [--no-verify]", file=sys.stderr)
        return 2
    project, outdir = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    cfg = load_config(None)
    if "--config" in sys.argv:
        cfg = load_config(sys.argv[sys.argv.index("--config") + 1])
    no_verify = "--no-verify" in sys.argv
    cfg["max_hotspots"] = int(sys.argv[sys.argv.index("--max-hotspots") + 1]) if "--max-hotspots" in sys.argv else cfg["max_hotspots"]

    review_md = Path(__file__).parent / "prompts/review.md"
    tpl = review_md.read_text() if review_md.is_file() else "你是资深代码审核员。"
    review_ctx = ""
    for cand in [project / "REVIEW.md", Path.home() / ".config/graph-code-review/REVIEW.md"]:
        if cand.is_file():
            review_ctx = f"\n## 项目审核约定（REVIEW.md）\n{cand.read_text()[:3000]}\n"
            break

    items = parse_evidence(outdir)
    hotspots = build_hotspots(items, project, cfg["budget_chars"])
    print(f"证据 {len(items)} 条 → 热点组 {len(hotspots)} 个（上限 {cfg['max_hotspots']}）")
    if not hotspots:
        (outdir / "llm_review.md").write_text("# LLM 审核\n\n（无确定性证据可审——先跑 cbm_scan.sh）\n")
        return 0

    findings, raw_all = [], []
    for hi, hs in enumerate(hotspots[:cfg["max_hotspots"]], 1):
        prompt = f"{tpl}{review_ctx}\n\n## 待审热点 {hi}\n{hs}\n\n只输出 JSON 数组，无其它文字。"
        print(f"  review [{hi}/{min(len(hotspots), cfg['max_hotspots'])}] backend={cfg['backend']} model={cfg['review_model'] or '默认'}")
        out = llm_call(cfg, prompt)
        if not out:
            continue
        raw_all.append(out)
        arr = None
        try:
            arr = clean_llm_json(out)
        except (json.JSONDecodeError, ValueError):
            arr = None
        if arr is None:
            # 一次重试：模型话痨/格式漂移时强约束（Anthropic 实践——可运行的验证重于口说）
            out2 = llm_call(cfg, prompt + "\n\n【重申】上一条如果包含分析过程请作废。现在只输出 JSON 数组本身，第一个字符必须是 [，最后一个字符必须是 ]。无发现输出 []。")
            if out2:
                raw_all.append(out2)
                try:
                    arr = clean_llm_json(out2)
                except (json.JSONDecodeError, ValueError):
                    arr = None
        if isinstance(arr, dict):
            arr = arr.get("findings", [])
        if isinstance(arr, list):
            findings.extend([x for x in arr if isinstance(x, dict)])
        else:
            print("  JSON 解析失败，该组降级为 raw 留档")
    if raw_all:
        (outdir / "llm_raw.txt").write_text("\n\n=====\n\n".join(raw_all))

    # 核验（跨模型更佳）：逐条投票，假阳性丢弃
    verified, dropped = 0, 0
    if cfg.get("verify_model") and not no_verify and findings:
        print(f"  verify: model={cfg['verify_model']}（{len(findings)} 条逐条核验）")
        vcfg = dict(cfg)
        vm = cfg["verify_model"]
        if ":" in vm:
            vb, vmodel = vm.split(":", 1)
            vcfg["backend"], vcfg["review_model"] = vb, vmodel
        kept = []
        for f in findings:
            fp = Path(project) / str(f.get("file", ""))
            ex = ""
            if fp.is_file():
                ln = int(f.get("line", 0) or 0)
                lines = fp.read_text(errors="replace").splitlines()
                ex = "\n".join(lines[max(0, ln-8):ln+8])
            vprompt = (f"核验以下代码审核发现是否真实成立（对照代码摘录）。只输出 JSON: "
                       f"{{\"verdict\":\"true_positive|false_positive|uncertain\",\"reason\":\"...\"}}\n"
                       f"发现: {json.dumps(f, ensure_ascii=False)}\n代码摘录:\n{ex}")
            out = llm_call(vcfg, vprompt)
            v = {}
            try:
                v = clean_llm_json(out) or {}
            except (json.JSONDecodeError, ValueError):
                pass
            verdict = v.get("verdict", "uncertain")
            f["verdict"] = verdict
            f["verdict_reason"] = (v.get("reason") or "")[:80]
            if verdict == "false_positive":
                dropped += 1
            else:
                if verdict == "true_positive":
                    verified += 1
                kept.append(f)
        findings = kept

    md = ["# LLM 审核（证据驱动，热点 ±40 行）", "",
          f"backend={cfg['backend']}  review_model={cfg['review_model'] or '默认'}  "
          f"verify_model={cfg.get('verify_model') or '关'}", "",
          f"原始确定性证据 {len(items)} 条 → 热点组 {min(len(hotspots), cfg['max_hotspots'])} 个 → "
          f"findings {len(findings)} 条（核验丢弃假阳性 {dropped}，确认 {verified}，其余未核验/不确定）。", ""]
    if findings:
        md += ["| 级别 | 位置 | 问题 | 建议修复 | 核验 |", "|---|---|---|---|---|"]
        sev_rank = {"Important": 0, "Nit": 1}
        for f in sorted(findings, key=lambda x: sev_rank.get(x.get("severity", ""), 2)):
            md.append(f"| {f.get('severity','?')} | {f.get('file','?')}:{f.get('line','?')} "
                      f"| {(f.get('issue','') or '')[:70]} | {(f.get('fix','') or '')[:50]} "
                      f"| {str(f.get('verdict','-')).replace('|','/')} |")
        md.append("\n每条必须可溯源到热点摘录；`Pre-existing` 标注表示非本次引入。"
                  "\n性能类问题 LLM 召回率极低（arXiv 2606.15689），请以 profile 工具为准。")
    else:
        md.append("**无发现。**（确定性证据均已审，无需要修复项——如无发现请直说，不硬凑）")
    (outdir / "llm_review.md").write_text("\n".join(md) + "\n")
    print(f"[DONE] {outdir/'llm_review.md'}: findings {len(findings)}（核验丢 {dropped}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
