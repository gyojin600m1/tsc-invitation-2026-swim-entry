#!/bin/zsh
cd "$(dirname "$0")/.."
echo "=== 東京SC招待2026 速報の見張り ==="
echo "最近の記録:"; tail -8 watch/log.txt 2>/dev/null || echo " まだありません"
echo; echo "いま取り込みます…"
/Library/Developer/CommandLineTools/usr/bin/python3 watch/update.py
echo "---"; tail -3 watch/log.txt 2>/dev/null
echo; echo "公開ページ: https://gyojin600m1.github.io/tsc-invitation-2026-swim-entry/"
read -k1 "?終わるにはキーを押してください"
