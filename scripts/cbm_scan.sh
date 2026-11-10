#!/usr/bin/env bash
# CBM 代码审核 skill — 阶段1 建图+证据提取（零 LLM）
# 用法: cbm_scan.sh <目标绝对路径> [outdir]
# 产物: <outdir>/{routes.txt,deadcode_raw.txt,deadcode_verified.md,similar.md,danger.txt} + cbm-cache/
set -uo pipefail

TARGET="${1:?用法: cbm_scan.sh <目标绝对路径> [outdir]}"
OUTDIR="${2:-$(pwd)/cbm-review}"
BIN="$(command -v codebase-memory-mcp || true)"
[ -n "$BIN" ] && [ -x "$BIN" ] || BIN="$HOME/cbm/bin/codebase-memory-mcp"

[ -x "$BIN" ] || { echo "FAIL: codebase-memory-mcp 不存在，先安装（见 SKILL.md 环境自检）" >&2; exit 2; }
[ -d "$TARGET" ] || { echo "FAIL: 目标不存在 $TARGET" >&2; exit 2; }
TARGET="$(cd "$TARGET" && pwd)"

export CBM_CACHE_DIR="${CBM_CACHE_DIR:-$OUTDIR/cbm-cache}"
OUTDIR="${OUTDIR:-$(dirname "$TARGET")/cbm-review}"
export CBM_CACHE_DIR="$OUTDIR/cbm-cache"
mkdir -p "$OUTDIR" "$CBM_CACHE_DIR"
chmod -R go-w "$CBM_CACHE_DIR" 2>/dev/null

echo "[1/5] 建图 $TARGET"
"$BIN" cli --quiet index_repository --repo-path "$TARGET" --quiet || { echo "FAIL: 建图"; exit 3; }

PROJ=$("$BIN" cli --quiet list_projects 2>/dev/null | awk -v t="$TARGET" '$2==t {print $1; exit}')
[ -z "$PROJ" ] && { echo "FAIL: 目标未注册，list_projects:"; "$BIN" cli --quiet list_projects; exit 3; }
echo "PROJ=$PROJ"

echo "[2/5] 死代码候选"
"$BIN" cli --quiet query_graph --project "$PROJ" --query \
  'MATCH (f:Function) WHERE NOT EXISTS {(f)<-[:CALLS]-()} AND NOT EXISTS {(f)<-[:USAGE]-()} RETURN f.name, f.file, f.line LIMIT 100' \
  > "$OUTDIR/deadcode_raw.txt" 2>/dev/null

echo "[2b] 误报过滤（装饰器注册/self.调用/嵌套闭包/测试文件）"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 -I "$SCRIPT_DIR/filter_deadcode.py" "$OUTDIR/deadcode_raw.txt" "$TARGET" > "$OUTDIR/deadcode_verified.md" 2>/dev/null \
  || echo "(过滤脚本失败，deadcode_raw.txt 为原始候选)" > "$OUTDIR/deadcode_verified.md"

echo "[2c] 重复克隆 jscpd（Type-1/2，跳过 vendored）"
JSCPD="$(command -v jscpd || echo "$HOME/.local/bin/jscpd")"
if [ -x "$JSCPD" ]; then
  "$JSCPD" "$TARGET" -k 50 -r json -o "$OUTDIR/jscpd-raw" \
    -i "**/site-packages/**,**/lib/**,**/node_modules/**,**/.venv/**,**/venv/**,**/__pycache__/**,**/build/**,**/dist/**,**/.git/**,**/static/lib/**" \
    >/dev/null 2>&1
  python3 -I "$SCRIPT_DIR/jscpd_digest.py" "$OUTDIR/jscpd-raw/jscpd-report.json" > "$OUTDIR/jscpd.md" 2>/dev/null \
    || echo "(jscpd digest 生成失败，原始 JSON 在 jscpd-raw/)" > "$OUTDIR/jscpd.md"
else
  echo "(jscpd 未安装，跳过——npm i -g --prefix ~/.local jscpd 后重跑)" > "$OUTDIR/jscpd.md"
fi

echo "[2d] 守卫/权限面 zift（嵌入式鉴权点清单，未装自动跳过）"
ZIFT="$(command -v zift || echo "$HOME/.local/bin/zift")"
if [ -x "$ZIFT" ]; then
  "$ZIFT" scan "$TARGET" -f json -o "$OUTDIR/zift.json" >/dev/null 2>&1
  python3 -I "$SCRIPT_DIR/authz_digest.py" "$OUTDIR/zift.json" "$OUTDIR/febe.txt" "$TARGET" > "$OUTDIR/authz.md" 2>/dev/null \
    || echo "(authz digest 生成失败，原始 JSON 在 zift.json)" > "$OUTDIR/authz.md"
else
  echo "(zift 未安装，跳过——install.sh 会装)" > "$OUTDIR/authz.md"
fi

echo "[2e] CSS 审计 stylelint（核心规则集，未装自动跳过）"
SL="$(command -v stylelint || echo "$HOME/.local/bin/stylelint")"
if [ -x "$SL" ]; then
  (cd "$TARGET" && find . -name '*.css' -not -path '*/static/lib/*' -not -path '*/node_modules/*' \
     -not -path '*/.venv/*' -not -path '*/site-packages/*' -not -path '*/build/*' -not -path '*/dist/*' \
     -not -name '.*' | sed 's|^\./||' | head -50 > "$OUTDIR/_css_files.txt")
  if [ -s "$OUTDIR/_css_files.txt" ]; then
    (cd "$TARGET" && "$SL" $(cat "$OUTDIR/_css_files.txt" | tr '\n' ' ') \
      --config "$SCRIPT_DIR/.stylelintrc.json" -f json -o "$OUTDIR/stylelint.json") >/dev/null 2>&1
    python3 -I "$SCRIPT_DIR/stylelint_digest.py" "$OUTDIR/stylelint.json" > "$OUTDIR/stylelint.md" 2>/dev/null \
      || echo "(stylelint digest 生成失败，原始 JSON 在 stylelint.json)" > "$OUTDIR/stylelint.md"
  else
    echo "（目标无 CSS 文件——跳过）" > "$OUTDIR/stylelint.md"
  fi
  rm -f "$OUTDIR/_css_files.txt"
else
  echo "(stylelint 未安装，跳过——install.sh 会装)" > "$OUTDIR/stylelint.md"
fi

echo "[3] 相似代码（不带 WHERE——cbm 的 WHERE 子句会静默返回空；tests 过滤在下游 python 做）"
"$BIN" cli --quiet query_graph --project "$PROJ" --query \
  'MATCH (a)-[s:SIMILAR_TO]->(b) RETURN a.name, a.file, b.name, b.file LIMIT 30' \
  > "$OUTDIR/similar.txt" 2>/dev/null

echo "[4] 危险模式"
"$BIN" cli --quiet search_code --project "$PROJ" \
  --pattern 'shell\s*=\s*True|yaml\.load\(|pickle|verify=False|md5|os\.system|eval\(|mktemp|except\s+Exception:\s*$|read_text\(\)' \
  --regex true > "$OUTDIR/danger.txt" 2>/dev/null

echo "[4b] 安全扫描四件套（bandit/泄露/依赖CVE/opengrep——未装自动跳过）"
BANDIT="$(command -v bandit || echo "$HOME/.local/bin/bandit")"
LEAKS="$(command -v betterleaks || echo "$HOME/.local/bin/betterleaks")"
OSV="$(command -v osv-scanner || echo "$HOME/.local/bin/osv-scanner")"
OG="$(command -v opengrep || echo "$HOME/.local/bin/opengrep")"
[ -x "$BANDIT" ] && "$BANDIT" -r "$TARGET" -q -f json -o "$OUTDIR/bandit.json" \
  -x "*/tests/*,*/.venv/*,*/venv/*,*/site-packages/*,*/build/*" 2>/dev/null || true
[ -x "$LEAKS" ] && "$LEAKS" dir "$TARGET" --report-format json --report-path "$OUTDIR/leaks.json" \
  --no-banner >/dev/null 2>&1 || true
[ -x "$OSV" ] && "$OSV" scan source -r "$TARGET" --format json --output-file "$OUTDIR/osv.json" \
  --allow-no-lockfiles --verbosity error >/dev/null 2>&1 || true
if [ -x "$OG" ]; then
  (cd "$TARGET" && timeout 600 "$OG" scan --config "$SCRIPT_DIR/numeric-display.yaml" --json \
    -o "$OUTDIR/og_custom.json" --exclude "static/lib" --exclude "*.min.js" --exclude "node_modules" >/dev/null 2>&1) || true
  (cd "$TARGET" && timeout 600 "$OG" scan --config auto --json \
    -o "$OUTDIR/og_auto.json" --exclude "static/lib" --exclude "*.min.js" --exclude "node_modules" >/dev/null 2>&1) || true
fi
python3 -I "$SCRIPT_DIR/security_digest.py" "$OUTDIR/bandit.json" "$OUTDIR/leaks.json" \
  "$OUTDIR/osv.json" "$OUTDIR/og_auto.json" "$OUTDIR/og_custom.json" "$TARGET" > "$OUTDIR/security.md" 2>/dev/null || \
  echo "(security digest 生成失败，原始 JSON 已落盘)" > "$OUTDIR/security.md"

echo "[5] 架构/路由/热点"
"$BIN" cli --quiet get_architecture --project "$PROJ" > "$OUTDIR/routes.txt" 2>/dev/null

echo "[6] 跨文件调用图（同文件内部调用不上图）"
"$BIN" cli --quiet query_graph --project "$PROJ" --query \
  'MATCH (a:Function)-[:CALLS]->(b:Function) RETURN a.name, a.file, b.name, b.file LIMIT 2000' \
  > "$OUTDIR/calls_raw.txt" 2>/dev/null
python3 -I "$SCRIPT_DIR/callgraph.py" "$OUTDIR/calls_raw.txt" "$OUTDIR" "$PROJ" || true

echo "[DONE] 证据落盘 $OUTDIR：routes/deadcode/similar/danger/security/febe/graph"
ls -la "$OUTDIR"
