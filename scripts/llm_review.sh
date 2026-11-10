#!/usr/bin/env bash
# LLM 审核阶段（可选——需先配置 ~/.config/graph-code-review/llm.json）
# 用法: llm_review.sh <项目绝对路径> <outdir> [--no-verify] [--config llm.json]
set -uo pipefail
P="${1:?用法: llm_review.sh <项目> <outdir> [--no-verify]}"; O="${2:?缺少 outdir}"
CFG="$HOME/.config/graph-code-review/llm.json"
[[ "$*" == *--config* ]] && CFG="${4:-$CFG}"
if [ ! -f "$CFG" ] && [ ! -f "$HOME/.config/graph-code-review/llm.json" ]; then
  echo "# LLM 审核\n\n（未配置——复制 scripts/llm.json.example 到 ~/.config/graph-code-review/llm.json 并填后端）" > "$O/llm_review.md"
  echo "未配置 LLM（~/.config/graph-code-review/llm.json），常规审核产物不受影响"
  exit 0
fi
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 -I "$SCRIPT_DIR/llm_review.py" "$P" "$O" --config "$CFG" "$@"
