---
name: graph-code-review
description: 基于 CBM（codebase-memory-mcp）调用图的代码审核基准管线：传统方法建图（零 LLM）→ 确定性查询（死代码/相似代码/危险模式）→ FE-BE 接口对账 → 只把热点上下文喂给 LLM 出审核意见。凡需"审核项目代码、检查类/函数调用是否正确、前后端接口是否一致、找死代码或相似代码、给优化建议"时使用。
---

# CBM 代码审核 skill（图驱动代码审核基准）

调用 CBM（codebase-memory-mcp 0.12.1+）做代码审核。核心思想：**建图与证据提取零 LLM（CBM CLI），
LLM 只读"热点上下文"出审核意见**。在多个 FastAPI/Flask 项目上实战验证。

## 0. 铁律

1. 目标项目**只读**：禁写、禁安装、禁改文件；产物只写 outdir（默认 `<工作区>/cbm-review/`）。
2. 已知坑位（实测）：
   - 缓存目录 0775 触发 daemon 告警 → 先 `chmod -R go-w $CBM_CACHE_DIR`
   - `--format` 只能用于查询类工具；`index_repository` 只接受外层 `--json`
   - `--quiet` 下 `get_graph_schema` 输出为空，不用它
   - 前端 minified 库（vue.esm-browser.js 类）会进图 → 报告前排除（噪音来源）

## 环境自检

```bash
BIN="$(command -v codebase-memory-mcp || echo "$HOME/cbm/bin/codebase-memory-mcp")"
"$BIN" --version            # 期望 0.12.1
export CBM_CACHE_DIR=<outdir>/cbm-cache
mkdir -p "$CBM_CACHE_DIR" && chmod -R go-w "$CBM_CACHE_DIR"
```

二进制不存在：`bash "$HOME/cbm/bin/install.sh" --dir=<dir> --skip-config`（自包含下载器，GitHub releases 拉取，release 约百 MB，300s 超时转后台等完成；install.sh 已随二进制保留在 ~/cbm/bin/）。

## 阶段 1 建图 + 证据提取（零 LLM，一条命令）

```bash
bash "$(dirname scripts/cbm_scan.sh 2>/dev/null || echo .)/scripts/cbm_scan.sh" <目标绝对路径> [outdir]
```

脚本自动完成：增量建图（`index_repository`）→ 项目名解析 → 四类确定性查询全部落盘到 out 目录：

| 证据文件 | 内容 |
|---|---|
| `routes.txt` | 路由/架构面（get_architecture） |
| `deadcode_raw.txt` | 死代码候选（含误报） |
| `deadcode_verified.md` | **过滤后死代码**（filter_deadcode.py：grep 全仓验证零调用） |
| `similar.md` | `SIMILAR_TO` 相似代码边（排除 tests/ 与同文件） |
| `danger.txt` | 危险模式 regex 命中（file+line） |
| `security.md` | 安全四件套汇总：bandit（Python AST）+ betterleaks（密钥，含备份/zip 内）+ osv-scanner（依赖 CVE）+ opengrep（auto 462 规则 + 自定义数字格式规则）；各源缺失自动占位 |
| `graph.html` | **跨文件调用图**（自包含交互力导向图，canvas 无依赖；只画 a.file<>b.file 的 CALLS，同文件内部调用不上图；函数节点>350 自动降级文件级；拖动/悬停高亮） |
| `graph.mmd` | Mermaid 文件级 flowchart（带调用次数，贴进任何 mermaid 渲染器即可） |
| `graph.dot` | DOT 函数级（按文件 cluster，graphviz 渲染） |
| `febe_report.md` | 路由 × 前端调用 对账（见阶段 3；remote_audit 另存 febe.txt） |
| `authz.md` | 守卫/权限面清单（zift 阶段 [2d]：嵌入式鉴权点分类计数 + top 命中；零命中≈系统性缺失信号） |
| `stylelint.md` | CSS 审计（stylelint 阶段 [2e]，核心规则集零依赖：非法 hex/空块/重复声明/未知属性等；无 CSS 自动跳过） |
| `llm_review.md` | **LLM 审核**（可选模式，见下节：证据驱动热点 + 跨模型核验；未配置时占位不阻塞） |
| `calls.txt` | 跨文件调用清单（CALLS 边：调用方→被调方，vendor 过滤） |
| `jscpd.md` | 重复克隆对（kucherenko/jscpd token 级 Rabin-Karp，自动排除 vendored/tests；未装则提示） |

手工重跑任一查询（项目名 = `cli --quiet list_projects` 的 name 列）：

```bash
query_graph --project <p> --query 'MATCH (f:Function) WHERE NOT EXISTS {(f)<-[:CALLS]-()} AND NOT EXISTS {(f)<-[:USAGE]-()} RETURN f.name, f.file, f.line LIMIT 100'
query_graph --project <p> --query 'MATCH (a)-[s:SIMILAR_TO]->(b) RETURN a.name, a.file, b.name, b.file LIMIT 30'
get_architecture --project <p>
search_code --project <p> --pattern 'shell\s*=\s*True|yaml\.load\(|pickle|verify=False|md5|os\.system|eval\(|mktemp|except\s+Exception:\s*$|read_text\(\)' --regex true
```

**坑位：CBM Cypher 的 WHERE 子句过滤文件（`a.file <> b.file`、`CONTAINS`）会静默返回空——**
**所有文件级过滤（tests/、minified、同文件）一律在下游 python 里做。**

结果可视化（Lens UI）：`nohup bash -c 'tail -f /dev/null | "$0" --ui=true --port=9749' "$BIN" >/tmp/cbm-ui.log 2>&1 &`
daemon 随叫随到、最后客户端断开即自杀，保活靠常驻 stdin（tail -f）；服务起后浏览器开 `http://127.0.0.1:9749`（CBM Lens Beta）。

## 阶段 3 FE/BE 接口对账（传统方法，零 LLM）

```bash
python3 -I scripts/febe_audit.py <target_path> [outdir]   # 路由装饰器表 × 前端 fetch URL 集合差
```

- regex 提取 FastAPI/Flask 路由装饰器 × 前端 `fetch/axios/url` 请求路径（路径参数统一规范化为 `:p`）
- 后端路由无前端调用 → 疑似死接口；前端调用无后端路由 → 潜在不一致
- 未匹配项进入阶段 4 定案（可能动态拼接 URL，需人工/LLM 判断）

## 阶段 4 LLM 审核（token 预算内，本会话直接执行）

只把以下"热上下文"喂给 LLM（直接读码，禁止重读全仓）：

1. 未匹配 FE/BE 对（每对 ±40 行）
2. get_architecture hotspots 中 auth / exec / forward / file-io 面
3. 死代码过滤后仍存疑的候选（grep 证据矛盾的）
4. SIMILAR_TO top 簇（判定可否合并）
5. 前端热点附带 `scripts/ui-guidelines.md` 清单（Web Interface Guidelines，MIT）逐项核对：焦点管理/loading 态/键盘可达/金额精度/空值展示

产出 findings：`severity(P0-P3) | f:行 | 问题 | 证据 | 建议`（喂给 LLM 的是上下文，非全仓）。

## 远端审核（打包上传模式）

目标机装不了/不想落产物时：`bash scripts/remote_audit.sh <本地项目绝对路径> <user@host> [outdir]`
打包（排除 .git/.venv/site-packages/node_modules/.env/*.db/*.log，>200MB 拒发）→ scp → 远端解压跑
cbm_scan.sh+febe_audit.py → rsync 取回 → 清理远端。远端 skill 需已部署（~/.agents/skills 或 ~/.zcode/skills），
CBM 二进制在 PATH 或 ~/cbm/bin（本 skill 部署基线：user@host（示例））。Type-4 语义检测（--semantic）需 548MB
模型，默认不随包走、远端不跑。

## 安全工具安装（阶段 4b 全可选，缺哪个跳哪个；jscpd 见下）

```bash
pipx install bandit                                    # Python AST 安全（Apache-2.0）
curl -sL https://github.com/betterleaks/betterleaks/releases/download/v1.10.0/betterleaks_1.10.0_linux_x64.tar.gz | tar xz -C ~/.local/bin betterleaks   # 密钥（MIT）
curl -sL -o ~/.local/bin/osv-scanner https://github.com/google/osv-scanner/releases/download/v2.7.0/osv-scanner_linux_amd64 && chmod +x ~/.local/bin/osv-scanner  # 依赖 CVE（Apache-2.0）
curl -sL -o ~/.local/bin/opengrep https://github.com/opengrep/opengrep/releases/download/v1.33.1/opengrep_manylinux_x86 && chmod +x ~/.local/bin/opengrep  # 模式引擎（LGPL-2.1）
npm i -g --prefix ~/.local jscpd                       # 克隆检测阶段 [2c]（MIT；需 node/npm）
```

- opengrep 双跑：`--config auto`（注册表 462 规则，首跑联网、之后离线缓存）+ `--config scripts/numeric-display.yaml`（浮点长尾守卫：格式化又回转 Number/模板串裸除法/f-string 裸除法/百分比无格式化/JSX 具名标签除法）
- opengrep 坑：YAML pattern 含反引号必须整体加引号；JSX 通配标签 `<$T>` 词法报错，只支持具名标签
- 数字格式规则 0 命中≠规则失效（合成样例 7/8 验真过），是回归守卫

## 业务检查框架（AGHAST，可选——四组检查项落地路径）

AGHAST static 检查 = opengrep 规则 + check.json 元数据（severity/confidence），无 LLM 可跑，3 秒级：
```bash
aghast scan <项目绝对路径> --config-dir <checks目录> --output-format json -o out.json
```
- checks 目录结构：`checks-config.json`（checks[].id/repositories/**精确匹配 scan 给定路径**/enabled）+ `checks/<id>/<id>.json` + 同名 .yaml（opengrep 规则）
- "缺守卫"模板（数据一致性/权限一致性通用）：pattern=路由装饰器+def，**pattern-not=项目真实守卫**（require/Depends/自定义装饰器），metavariable-regex 排除 healthz 类
- 实测：可在数秒内精确列出缺守卫端点全清单（无 LLM）；四组检查项映射：权限一致性=守卫覆盖、状态流转=loading/submit 复位（LLM 检查更强）、数据一致性=前后端字段枚举、展示正确性=numeric-display.yaml
- AI 检查（repository/targeted）需 --agent-provider（claude-code 或 opencode+本地模型）——opt-in；本仓库不分发 aghast（AGPL-3.0，子进程调用）

## LLM 审核（可选模式，需配置）

```bash
cp scripts/llm.json.example ~/.config/graph-code-review/llm.json   # 填 backend/model
bash scripts/llm_review.sh <项目> <outdir>                         # 在 cbm_scan.sh 之后跑
```
- 后端: `opencode`（默认模型实测可用）| `claude` | `ollama` | `openai`（兼容 /chat/completions）
- 流程: 确定性证据 → 热点 ±40 行（有界预算）→ review 模型出 JSON findings → [可选] verify 模型逐条核验丢假阳性
- `REVIEW.md`（项目根）= 业务审核约定，自动注入——AGHAST 式"项目检查项"的落点；LLM 提示词遵循 Anthropic 实践（file:line 证据、cap nits、计数开头、Pre-existing 标注、无发现直说）
- 前端热点自动挂 `prompts/design-audit.md` rubric（vendor 自 Impeccable: 无障碍/性能/主题/响应式 5 维）；纯风格类发现被丢弃
- **已知边界**（arXiv 2606.15689 实证）: 性能类 bug 各模型近零召回（用 profile 工具）；摘录>150 行 F1 崩到 0.043（本管线 ±40 行设计规避）；小模型常优于大模型且 3.2x 便宜
- 增益实证（tokenmon）: LLM 在 bandit 仅报 B608 注入的同一位置，额外发现"标识符未加引号→语法报错→except 静默吞→漏采数据"的正确性问题——读码坐实

## 视觉审查（web-design-reviewer 复刻，agent 流程）

目标应用可运行时：`bash scripts/visual_capture.sh <outdir> http://localhost:PORT/...`
（headless chrome 桌面 1440x900 + 移动 390x844 双尺寸截图 → outdir/screenshots/）
随后 agent 用 Read 看截图 + 对照 `scripts/ui-guidelines.md` + 源码定位，产出报告；不动被审代码。

## 报告格式（<outdir>/<name>_review.md）

五节：1 调用正确性 2 接口一致性 3 死代码 4 相似代码 5 优化建议。每条 finding 必含
file:line + severity(P0-P3) + 源码证据；确定性发现与 LLM 判断分开标注。
