#!/usr/bin/env bash
# remote_audit.sh — 本地代码打包→远端跑 CBM 审核→取回 findings（零 LLM）
# 用法: remote_audit.sh <本地项目绝对路径> <user@host> [outdir]
# 产物: <outdir>/ 与远端 <name>-out/ 同构（routes/deadcode/similar/danger + febe）
# 前提: 远端已部署本 skill（~/.agents/skills 或 ~/.zcode/skills）且 CBM 二进制在 PATH 或 ~/cbm/bin
set -uo pipefail

LOCAL="${1:?用法: remote_audit.sh <本地项目绝对路径> <user@host> [outdir]}"
DEST="${2:?缺少 user@host}"
OUTDIR="${3:-$(pwd)/cbm-remote-review}"

[ -d "$LOCAL" ] || { echo "FAIL: 本地路径不存在 $LOCAL" >&2; exit 2; }
LOCAL="$(cd "$LOCAL" && pwd)"
NAME="$(basename "$LOCAL")"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "[1/5] 打包 $NAME（防密钥：排除 .env/*.db/*.log）"
tar -czf "$WORK/$NAME.tgz" -C "$(dirname "$LOCAL")" \
  --exclude='.git' --exclude='__pycache__' --exclude='.venv' --exclude='venv' \
  --exclude='site-packages' --exclude='node_modules' --exclude='build' \
  --exclude='dist' --exclude='cbm-review' --exclude='cbm-cache' \
  --exclude='cbm-remote-review' --exclude='.env' --exclude='.env.*' \
  --exclude='*.db' --exclude='*.log' \
  "$NAME" || { echo "FAIL: tar 打包" >&2; exit 3; }
TGZ_KB=$(( $(stat -c%s "$WORK/$NAME.tgz") / 1024 ))
echo "包大小 ${TGZ_KB}KB"
[ "$TGZ_KB" -gt 204800 ] && { echo "FAIL: 包超 200MB，补排除规则后重试" >&2; exit 3; }

echo "[2/5] 上传 → $DEST"
ssh -o BatchMode=yes "$DEST" 'mkdir -p ~/cbm-audit-in' || { echo "FAIL: ssh 不通" >&2; exit 4; }
scp -q "$WORK/$NAME.tgz" "$DEST:cbm-audit-in/$NAME.tgz"

echo "[3/5] 远端解压+审核（scan + febe）"
SKILL_DIR='$(command -v codebase-memory-mcp >/dev/null && echo ok)'
ssh -o BatchMode=yes "$DEST" "set -e
  cd ~/cbm-audit-in && rm -rf '$NAME' '$NAME-out' && mkdir -p '$NAME'
  tar -xzf '$NAME.tgz' -C '$NAME' --strip-components=1
  S=~/.agents/skills/graph-code-review/scripts
  [ -d \$S ] || S=~/.zcode/skills/graph-code-review/scripts
  bash \$S/cbm_scan.sh ~/cbm-audit-in/'$NAME' ~/cbm-audit-in/'$NAME-out'
  python3 -I \$S/febe_audit.py ~/cbm-audit-in/'$NAME' > ~/cbm-audit-in/'$NAME-out'/febe.txt 2>/dev/null || true"

echo "[4/5] 取回 findings"
mkdir -p "$OUTDIR"
rsync -az "$DEST:cbm-audit-in/$NAME-out/" "$OUTDIR/"

echo "[5/5] 远端清理"
ssh -o BatchMode=yes "$DEST" "rm -rf ~/cbm-audit-in/'$NAME' ~/cbm-audit-in/'$NAME-out' ~/cbm-audit-in/'$NAME.tgz'"
echo "[DONE] $OUTDIR："
ls -la "$OUTDIR"
