#!/usr/bin/env bash
# 视觉审查素材采集（web-design-reviewer 复刻——截图由 agent 看，报告由 agent 出）
# 用法: visual_capture.sh <outdir> <url> [url...]   （需目标应用已在运行；google-chrome headless）
set -uo pipefail
O="${1:?用法: visual_capture.sh <outdir> <url...>}"; shift
CHROME="$(command -v google-chrome || echo /usr/bin/google-chrome)"
[ -x "$CHROME" ] || { echo "google-chrome 不存在" >&2; exit 2; }
mkdir -p "$O/screenshots"
for url in "$@"; do
  name=$(echo "$url" | sed 's|https\?://||; s|[/:?=&]|_|g; s|_*$||' | cut -c1-40)
  "$CHROME" --headless=new --disable-gpu --window-size=1440,900 --screenshot="$O/screenshots/${name}_desktop.png" \
    --virtual-time-budget=8000 --hide-scrollbars "$url" >/dev/null 2>&1
  "$CHROME" --headless=new --disable-gpu --window-size=390,844 --screenshot="$O/screenshots/${name}_mobile.png" \
    --virtual-time-budget=8000 --hide-scrollbars "$url" >/dev/null 2>&1
  echo "✓ $url → ${name}_{desktop,mobile}.png"
done
echo "下一步: agent 用 Read 看截图 + 对照 scripts/ui-guidelines.md + 源码定位，产出 llm_review.md 附录（视觉审查节）"
