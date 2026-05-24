"""DB 백업 스크립트.

`data/questions.db` → `data/backups/questions_YYYYMMDD_HHMM.db`.
최근 14개만 유지 (옛 백업 자동 삭제).

수동 실행:
    python scripts/backup_db.py
또는 매 커밋 직전:
    python scripts/backup_db.py && git add data/questions.db && git commit ...
"""
from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
SRC = APP_DIR / "data" / "questions.db"
BACKUP_DIR = APP_DIR / "data" / "backups"
KEEP = 14


def backup() -> Path:
    if not SRC.exists():
        print(f"❌ 원본 DB 없음: {SRC}", file=sys.stderr)
        sys.exit(1)

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    dst = BACKUP_DIR / f"questions_{stamp}.db"

    # 같은 분 내 재실행 → 덮어쓰기
    shutil.copy2(SRC, dst)
    size_kb = dst.stat().st_size // 1024
    print(f"✅ 백업: {dst.name} ({size_kb} KB)")

    # 오래된 백업 정리 (최근 KEEP개만 유지)
    all_bk = sorted(BACKUP_DIR.glob("questions_*.db"))
    if len(all_bk) > KEEP:
        for old in all_bk[: len(all_bk) - KEEP]:
            old.unlink()
            print(f"   🗑  옛 백업 삭제: {old.name}")

    print(f"   보관 중: {min(len(all_bk), KEEP)}개")
    return dst


if __name__ == "__main__":
    backup()
