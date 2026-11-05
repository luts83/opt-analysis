#!/usr/bin/env bash
# Railway Volume 목록 — 비번 없는 id_ed25519_railway 만 쓰도록 강제
set -euo pipefail
cd "$(dirname "$0")/.."

unset RAILWAY_TOKEN

KEY="$HOME/.ssh/id_ed25519_railway"
OLD="$HOME/.ssh/id_ed25519"
OLD_PUB="$HOME/.ssh/id_ed25519.pub"
BAK_DIR="$HOME/.ssh/bak_railway_$$"

if [[ ! -f "$KEY" ]]; then
  ssh-keygen -t ed25519 -f "$KEY" -N "" -C "railway"
  echo "새 열쇠 생성: $KEY"
fi

# 예전 열쇠(비번 있음)를 잠깐 치워서 railway 가 그걸 고르지 못하게 함
mkdir -p "$BAK_DIR"
MOVED=0
if [[ -f "$OLD" ]]; then
  mv "$OLD" "$BAK_DIR/"
  MOVED=1
  echo "임시로 이동: id_ed25519 → $BAK_DIR"
fi
if [[ -f "$OLD_PUB" ]]; then
  mv "$OLD_PUB" "$BAK_DIR/"
  MOVED=1
fi

restore() {
  if [[ "$MOVED" -eq 1 ]]; then
    mv "$BAK_DIR"/* "$HOME/.ssh/" 2>/dev/null || true
    rmdir "$BAK_DIR" 2>/dev/null || true
    echo "예전 열쇠 복구 완료"
  fi
}
trap restore EXIT

echo "사용할 열쇠: $KEY"
echo "열쇠 고르라면 → id_ed25519_railway 선택 후 Yes"
echo "=== /data/snapshots ==="
npx --yes @railway/cli ssh -- ls -la /data/snapshots

echo ""
echo "=== 집계 ==="
npx --yes @railway/cli ssh -- python -c 'from pathlib import Path
r=Path("/data/snapshots")
print("ROOT", r, r.exists())
for t in ["IREN","TSLA","SPCX"]:
    f=sorted((r/t).glob("*.json")) if (r/t).exists() else []
    print(t, len(f), (f[0].stem if f else "-"), "~", (f[-1].stem if f else "-"))
    if f:
        print(" recent", ", ".join(x.stem for x in f[-6:]))
oe=r/"_learning"/"option_events.jsonl"
print("events", oe.exists(), (sum(1 for _ in oe.open()) if oe.exists() else 0))
'
