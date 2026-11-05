#!/usr/bin/env python3
"""Railway Volume(/data/snapshots) 현황 집계.

사용 (로컬 터미널에서 — 에이전트 샌드박스 밖에서):
  cd opt-analysis
  set -a && source .env && set +a
  npx --yes @railway/cli link          # 최초 1회: 프로젝트 선택
  npx --yes @railway/cli ssh --command 'python - <<EOF
  ... 또는 아래처럼 run:
  npx --yes @railway/cli run --service <봇서비스명> -- python scripts/railway_volume_stats.py --remote

운영 분석의 기준은 Railway Volume (/data/snapshots).
기본: --remote 없이 로컬 SNAPSHOTS_DIR 집계.
--via-ssh: railway ssh 로 원격 디렉터리 목록만 가져와 요약(파일 내용 없음).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path


def _root_snapshots(remote_hint: str | None = None) -> Path:
    if remote_hint:
        return Path(remote_hint)
    env = os.getenv("SNAPSHOTS_DIR", "snapshots")
    p = Path(env)
    if not p.is_absolute():
        p = Path(__file__).resolve().parent.parent / p
    return p


def summarize(root: Path) -> None:
    tickers = ["IREN", "TSLA", "SPCX"]
    print(f"ROOT: {root}  exists={root.exists()}")
    if not root.exists():
        return
    print("=" * 60)
    print("1) 일일 스냅샷")
    print("=" * 60)
    for t in tickers:
        d = root / t
        files = sorted(d.glob("*.json")) if d.exists() else []
        if not files:
            print(f"{t}: 0")
            continue
        modes = Counter()
        src = Counter()
        fb = 0
        for f in files:
            try:
                data = json.loads(f.read_text())
            except Exception:
                continue
            if (data.get("prediction_feedback") or {}).get("available"):
                fb += 1
            oe = data.get("option_event_report")
            if isinstance(oe, dict) and oe.get("mode"):
                modes[oe["mode"]] += 1
            elif data.get("report_mode"):
                modes[str(data["report_mode"])] += 1
            src[data.get("narrative_source") or "(없음)"] += 1
        print(
            f"{t}: {len(files)}일  ({files[0].stem} ~ {files[-1].stem})  feedback={fb}"
        )
        if modes:
            print(f"   mode: {dict(modes)}")
        print(f"   source: {dict(src)}")
        print(f"   최근: {', '.join(x.stem for x in files[-6:])}")

    print("\n" + "=" * 60)
    print("2) option_events.jsonl / patterns.json")
    print("=" * 60)
    oe = root / "_learning" / "option_events.jsonl"
    if oe.exists():
        lines = [ln for ln in oe.read_text().splitlines() if ln.strip()]
        by_t, results = Counter(), Counter()
        outcomes = []
        for ln in lines:
            r = json.loads(ln)
            by_t[r.get("ticker", "?")] += 1
            results[r.get("result") or "(none)"] += 1
            if r.get("outcome_date"):
                outcomes.append(str(r["outcome_date"]))
        print(f"events: {len(lines)}행  {dict(by_t)}")
        print(f"results: {dict(results)}")
        if outcomes:
            print(f"outcome: {min(outcomes)} ~ {max(outcomes)}")
    else:
        print("option_events.jsonl: 없음")

    pp = root / "_learning" / "patterns.json"
    if pp.exists():
        d = json.loads(pp.read_text())
        for pid, rec in (d.get("patterns") or {}).items():
            obs = rec.get("observations") or []
            print(f"pattern {pid}: obs={len(obs)}")
    else:
        print("patterns.json: 없음")

    print("\n" + "=" * 60)
    print("3) 용량")
    print("=" * 60)
    files = [p for p in root.rglob("*") if p.is_file()]
    print(f"파일 {len(files)} · {sum(p.stat().st_size for p in files)/1024/1024:.1f} MB")


def via_ssh() -> int:
    """railway ssh 로 원격 ls/집계 스크립트 실행."""
    remote_py = r"""
import json, os
from pathlib import Path
from collections import Counter
root = Path(os.environ.get("SNAPSHOTS_DIR", "/data/snapshots"))
print("REMOTE_ROOT", root, "exists", root.exists())
if not root.exists():
    raise SystemExit(0)
for t in ["IREN","TSLA","SPCX"]:
    files = sorted((root/t).glob("*.json")) if (root/t).exists() else []
    print(f"{t}: {len(files)}", (files[0].stem if files else "-"), "~", (files[-1].stem if files else "-"))
    if files:
        print("  recent:", ", ".join(f.stem for f in files[-8:]))
oe = root/"_learning"/"option_events.jsonl"
if oe.exists():
    n = sum(1 for ln in oe.open() if ln.strip())
    print(f"option_events.jsonl lines={n} bytes={oe.stat().st_size}")
else:
    print("option_events.jsonl MISSING")
pp = root/"_learning"/"patterns.json"
if pp.exists():
    d = json.loads(pp.read_text())
    for k,v in (d.get("patterns") or {}).items():
        print(f"pattern {k} obs={len(v.get('observations') or [])}")
else:
    print("patterns.json MISSING")
files = [p for p in root.rglob("*") if p.is_file()]
print(f"files={len(files)} MB={sum(p.stat().st_size for p in files)/1024/1024:.1f}")
"""
    cmd = [
        "npx",
        "--yes",
        "@railway/cli",
        "ssh",
        "--",
        "python",
        "-c",
        remote_py,
    ]
    print("실행:", " ".join(cmd[:5]), "...")
    env = os.environ.copy()
    # .env 로드 (토큰)
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists() and not env.get("RAILWAY_TOKEN"):
        for line in env_path.read_text().splitlines():
            if line.startswith("RAILWAY_TOKEN="):
                env["RAILWAY_TOKEN"] = line.split("=", 1)[1].strip().strip('"').strip("'")
    r = subprocess.run(cmd, env=env)
    return r.returncode


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", help="집계할 snapshots 경로")
    ap.add_argument(
        "--via-ssh",
        action="store_true",
        help="railway ssh 로 원격 /data/snapshots 집계",
    )
    args = ap.parse_args()
    if args.via_ssh:
        return via_ssh()
    summarize(_root_snapshots(args.path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
