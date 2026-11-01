#!/usr/bin/env bash
# AI 主機一鍵安裝（Linux：Ubuntu／Debian／Fedora／RHEL 系，需要 systemd）
#
#   sudo bash install.sh                         一步一步問
#   sudo bash install.sh --yes --lan --model qwen3.5:9b --ollama-version 0.40.13
#   bash install.sh --list-versions              列出 Ollama 最近的版本
#
# 選項：
#   --ollama-version V   指定 Ollama 版本（預設最新）
#   --port N             面板埠號（預設 8780）
#   --lan                讓同一個網路的手機／電腦也能連
#   --models-dir D       模型放哪裡（預設 /usr/share/ollama/.ollama/models）
#   --model M            裝好先下載的模型，可以給好幾次
#   --no-terminal        面板不要終端機
#   --skip-ollama        不裝／不更新 Ollama
#   --user U             面板用哪個帳號跑（預設是執行 sudo 的人；終端機指令也是這個帳號）
#   --prefix P           安裝位置（預設 /opt/ai-server-kit）
#   --yes                不問問題
set -euo pipefail
ORIG_ARGS=("$@")

PREFIX=/opt/ai-server-kit
PORT=8780
LAN=0
OLLAMA_VER=""
MODELS_DIR=""
MODELS=()
TERMINAL=true
SKIP_OLLAMA=0
YES=0
RUN_USER="${SUDO_USER:-${USER:-root}}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

say()  { printf '%s\n' "$*"; }
step() { printf '\n\033[36m▶ %s\033[0m\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*"; }
die()  { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }
ask()  { # ask 問題 預設值
  if [ "$YES" = 1 ] || [ ! -t 0 ]; then printf '%s' "$2"; return; fi
  local a; read -r -p "$1 " a || true; printf '%s' "${a:-$2}"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --ollama-version) OLLAMA_VER="${2#v}"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --lan) LAN=1; shift ;;
    --models-dir) MODELS_DIR="$2"; shift 2 ;;
    --model) MODELS+=("$2"); shift 2 ;;
    --no-terminal) TERMINAL=false; shift ;;
    --skip-ollama) SKIP_OLLAMA=1; shift ;;
    --user) RUN_USER="$2"; shift 2 ;;
    --prefix) PREFIX="$2"; shift 2 ;;
    --yes|-y) YES=1; shift ;;
    --list-versions)
      curl -fsSL "https://api.github.com/repos/ollama/ollama/releases?per_page=20" |
        python3 -c 'import json,sys; [print("  %-12s %s%s" % (r["tag_name"].lstrip("v"), r["published_at"][:10], "（測試版）" if r["prerelease"] else "")) for r in json.load(sys.stdin)]'
      exit 0 ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
    *) die "不認得的選項：$1（--help 看說明）" ;;
  esac
done

if [ "$(id -u)" -ne 0 ]; then
  command -v sudo >/dev/null || die "要用 root 執行"
  exec sudo bash "$0" ${ORIG_ARGS[@]+"${ORIG_ARGS[@]}"}
fi
id "$RUN_USER" >/dev/null 2>&1 || die "沒有這個帳號：$RUN_USER"
RUN_HOME="$(getent passwd "$RUN_USER" | cut -d: -f6)"
command -v systemctl >/dev/null || die "這台沒有 systemd，請手動執行：python3 app/server.py --config config.json"

printf '\n  AI 主機一鍵安裝（Linux）\n  ───────────────\n'

# ── 硬體 ──
step "檢查硬體"
RAM=$(awk '/MemTotal/ {print $2*1024}' /proc/meminfo)
VRAM=0
if command -v nvidia-smi >/dev/null 2>&1; then
  VRAM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | awk '{s+=$1} END {print s*1048576}')
  say "  NVIDIA 顯示卡：$(nvidia-smi --query-gpu=name --format=csv,noheader | paste -sd '、')（共 $((VRAM/1073741824)) GB）"
else
  warn "  沒有偵測到 NVIDIA 顯示卡（或驅動沒裝）：模型會用 CPU 跑（比較慢）。"
fi
say "  系統記憶體 $((RAM/1073741824)) GB"

# ── Python ──
PY=$(command -v python3 || true)
if [ -z "$PY" ] || ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)'; then
  step "安裝 Python 3"
  if command -v apt-get >/dev/null; then apt-get update -qq && apt-get install -y -qq python3
  elif command -v dnf >/dev/null; then dnf install -y -q python3
  elif command -v yum >/dev/null; then yum install -y -q python3
  else die "請先安裝 Python 3.8 以上"; fi
  PY=$(command -v python3)
fi

# ── 問問題 ──
if [ "$YES" != 1 ] && [ -t 0 ]; then
  step "設定（直接按 Enter 用預設值）"
  if [ "$SKIP_OLLAMA" = 0 ] && [ -z "$OLLAMA_VER" ]; then
    OLLAMA_VER=$(ask "  Ollama 版本 [Enter＝最新版，例如 0.40.13]" "")
    OLLAMA_VER="${OLLAMA_VER#v}"
  fi
  if [ "$LAN" = 0 ]; then
    a=$(ask "  讓同一個網路的手機／其他電腦也能連進面板？(y/N)" "n")
    case "$a" in [yY]*) LAN=1 ;; esac
  fi
  if [ ${#MODELS[@]} -eq 0 ]; then
    say "  要先下載一個模型嗎？依你的硬體推薦："
    mapfile -t REC < <("$PY" - "$HERE/app/catalog.json" "$VRAM" "$RAM" <<'EOF'
import json, sys
cat = json.load(open(sys.argv[1], encoding="utf-8"))
vram, ram = float(sys.argv[2]), float(sys.argv[3])
budget = vram if vram > 0 else min(ram * 0.5, 6e9)
c = [("%s:%s" % (m["name"], t["tag"]), t["size"]) for g in cat["groups"] if g["id"] == "chat" for m in g["models"] for t in m["tags"]]
for n, s in sorted([x for x in c if x[1] * 1.15 + 0.7e9 <= budget], key=lambda x: -x[1])[:3]:
    print("%s %.1f" % (n, s / 1e9))
EOF
)
    i=1
    for r in ${REC[@]+"${REC[@]}"}; do say "    $i. ${r% *}（${r#* } GB）"; i=$((i+1)); done
    say "    0. 先不要（之後在面板裡挑）"
    a=$(ask "  選號碼，或直接打模型名稱" "0")
    if [[ "$a" =~ ^[0-9]+$ ]]; then
      if [ "$a" -ge 1 ] && [ "$a" -le ${#REC[@]} ]; then r="${REC[$((a-1))]}"; MODELS=("${r% *}"); fi
    elif [ -n "$a" ]; then MODELS=("$a"); fi
  fi
fi

# ── Ollama ──
if [ "$SKIP_OLLAMA" = 0 ]; then
  step "Ollama"
  HAVE=""
  if command -v ollama >/dev/null; then HAVE=$(ollama --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | tail -1 || true); say "  已安裝 ${HAVE:-（版本不明）}"; fi
  if [ -z "$HAVE" ] || { [ -n "$OLLAMA_VER" ] && [ "$HAVE" != "$OLLAMA_VER" ]; }; then
    say "  用官方安裝腳本安裝 Ollama ${OLLAMA_VER:-最新版}…"
    curl -fsSL https://ollama.com/install.sh | OLLAMA_VERSION="$OLLAMA_VER" sh
  fi
  if [ -n "$MODELS_DIR" ]; then
    mkdir -p "$MODELS_DIR"
    chown -R ollama:ollama "$MODELS_DIR" 2>/dev/null || true
    mkdir -p /etc/systemd/system/ollama.service.d
    printf '[Service]\nEnvironment="OLLAMA_MODELS=%s"\n' "$MODELS_DIR" > /etc/systemd/system/ollama.service.d/ai-server-kit.conf
    systemctl daemon-reload
    systemctl restart ollama
    say "  模型位置：$MODELS_DIR"
  fi
  systemctl enable --now ollama >/dev/null 2>&1 || true
  for _ in $(seq 1 30); do curl -fs http://127.0.0.1:11434/api/version >/dev/null && break; sleep 1; done
  if curl -fs http://127.0.0.1:11434/api/version >/dev/null; then say "  Ollama 執行中"; else warn "  Ollama 還沒起來：sudo systemctl status ollama"; fi
fi

# ── 面板 ──
step "面板"
mkdir -p "$PREFIX/data"
[ -f "$PREFIX/data/panel.pid" ] && systemctl stop ai-server-kit 2>/dev/null || true
rm -rf "$PREFIX/app"
cp -r "$HERE/app" "$PREFIX/app"
find "$PREFIX/app" -name __pycache__ -type d -prune -exec rm -rf {} +
cp "$HERE/uninstall.sh" "$PREFIX/uninstall.sh"
chmod 755 "$PREFIX/uninstall.sh"

CFG="$PREFIX/config.json"
TOKEN=""
[ -f "$CFG" ] && TOKEN=$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("token",""))' "$CFG" 2>/dev/null || true)
[ -n "$TOKEN" ] || TOKEN=$("$PY" -c 'import secrets; a="ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"; print("".join(secrets.choice(a) for _ in range(24)))')
HOST=127.0.0.1; [ "$LAN" = 1 ] && HOST=0.0.0.0
"$PY" - "$CFG" "$HOST" "$PORT" "$TOKEN" "$TERMINAL" "$PREFIX/data" <<'EOF'
import json, sys
p, host, port, token, term, data = sys.argv[1:]
json.dump({"host": host, "port": int(port), "token": token, "terminal": term == "true",
           "ollama": "http://127.0.0.1:11434", "data_dir": data}, open(p, "w"), indent=2)
EOF
chown "$RUN_USER" "$CFG"; chmod 600 "$CFG"
chown -R "$RUN_USER" "$PREFIX/data"

cat > "$PREFIX/show-token.sh" <<EOF
#!/bin/sh
# 顯示面板網址與登入碼（要 sudo 或面板帳號 $RUN_USER）
"$PY" -c 'import json; c=json.load(open("$CFG")); print("面板：http://127.0.0.1:%s/\n登入碼：%s" % (c["port"], c["token"]))'
EOF
chmod 755 "$PREFIX/show-token.sh"

cat > /etc/systemd/system/ai-server-kit.service <<EOF
[Unit]
Description=AI Server Kit 面板
After=network-online.target ollama.service
Wants=network-online.target

[Service]
User=$RUN_USER
WorkingDirectory=$RUN_HOME
ExecStart=$PY $PREFIX/app/server.py --config $CFG
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable ai-server-kit >/dev/null 2>&1
systemctl restart ai-server-kit
OK=0
for _ in $(seq 1 20); do curl -fs "http://127.0.0.1:$PORT/api/ping" >/dev/null && { OK=1; break; }; sleep 1; done
if [ "$OK" = 1 ]; then say "  面板執行中（帳號 $RUN_USER）"; else warn "  面板沒有起來：sudo journalctl -u ai-server-kit -n 50"; fi

# ── 區網 ──
LAN_URLS=()
if [ "$LAN" = 1 ]; then
  for ip in $(hostname -I 2>/dev/null); do case "$ip" in *:*|127.*|169.254.*) ;; *) LAN_URLS+=("http://$ip:$PORT/") ;; esac; done
  if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then ufw allow "$PORT/tcp" >/dev/null && say "  ufw 已開放 $PORT"; fi
  if command -v firewall-cmd >/dev/null && firewall-cmd --state >/dev/null 2>&1; then
    firewall-cmd --permanent --add-port="$PORT/tcp" >/dev/null && firewall-cmd --reload >/dev/null && say "  firewalld 已開放 $PORT"
  fi
fi

# ── 先下載模型 ──
for m in ${MODELS[@]+"${MODELS[@]}"}; do
  if command -v ollama >/dev/null && curl -fs http://127.0.0.1:11434/api/version >/dev/null; then
    step "下載模型 $m"; ollama pull "$m" || warn "  $m 下載失敗，之後可以在面板重試"
  fi
done

printf '\n\033[32m  ✔ 安裝完成\033[0m\n'
say "  面板：http://127.0.0.1:$PORT/"
for u in ${LAN_URLS[@]+"${LAN_URLS[@]}"}; do say "  手機／其他電腦：$u"; done
printf '\033[33m  登入碼：%s\033[0m\n' "$TOKEN"
say "  （忘記的話：sudo $PREFIX/show-token.sh）"
if [ "$LAN" = 0 ]; then say "  只有這台能連。從別台用 SSH 轉發：ssh -L $PORT:127.0.0.1:$PORT 帳號@這台，再開 http://127.0.0.1:$PORT/"; fi
if [ "$LAN" = 1 ]; then say "  注意：區網連線沒有加密，只在內網用；要從外面連請用 Tailscale 之類的 VPN。"; fi
say ""
