#!/usr/bin/env bash
# 解除安裝 AI 主機面板（Linux）。預設保留 Ollama 和模型。
#   sudo bash uninstall.sh [--remove-ollama] [--prefix /opt/ai-server-kit] [--yes]
set -euo pipefail
PREFIX=/opt/ai-server-kit
REMOVE_OLLAMA=0
YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --remove-ollama) REMOVE_OLLAMA=1; shift ;;
    --prefix) PREFIX="$2"; shift 2 ;;
    --yes|-y) YES=1; shift ;;
    *) echo "不認得的選項：$1"; exit 1 ;;
  esac
done
[ "$(id -u)" -eq 0 ] || { echo "要用 sudo 執行"; exit 1; }
if [ "$YES" != 1 ]; then
  read -r -p "確定要移除 AI 主機面板（$PREFIX）？(y/N) " a
  case "$a" in [yY]*) ;; *) echo "取消"; exit 0 ;; esac
fi

systemctl disable --now ai-server-kit 2>/dev/null || true
rm -f /etc/systemd/system/ai-server-kit.service
PORT=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("port", 8780))' "$PREFIX/config.json" 2>/dev/null || echo 8780)
if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "$PORT/tcp"; then ufw delete allow "$PORT/tcp" >/dev/null || true; fi
if command -v firewall-cmd >/dev/null && firewall-cmd --state >/dev/null 2>&1; then
  firewall-cmd --permanent --remove-port="$PORT/tcp" >/dev/null 2>&1 && firewall-cmd --reload >/dev/null || true
fi
rm -rf "$PREFIX"
echo "已移除面板"

if [ "$REMOVE_OLLAMA" = 1 ]; then
  MD=$(systemctl show ollama -p Environment 2>/dev/null | grep -oE 'OLLAMA_MODELS=[^ ]+' | cut -d= -f2 || true)
  systemctl disable --now ollama 2>/dev/null || true
  rm -f /etc/systemd/system/ollama.service
  rm -rf /etc/systemd/system/ollama.service.d
  B=$(command -v ollama || true)
  [ -n "$B" ] && rm -f "$B"
  rm -rf /usr/local/lib/ollama /usr/lib/ollama
  echo "已移除 Ollama 程式。模型檔留在 ${MD:-/usr/share/ollama/.ollama/models}（不要的話自己刪，通常很大）"
fi
systemctl daemon-reload
echo "完成"
