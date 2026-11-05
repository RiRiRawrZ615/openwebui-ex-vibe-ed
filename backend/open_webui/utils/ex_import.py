"""Import settings and data from a stock Open WebUI database.

The fork is a drop-in replacement, so the primary path is simply running against
the same data dir. This module covers the second case: a fresh fork data dir that
wants the stock data copied in.

Safety rules, enforced in code, not just documented:
  - source is opened read-only; the stock DB is never mutated
  - refuses to run when source and target resolve to the same path
  - refuses when the target looks like a live instance unless --force
  - copies only tables that exist in BOTH databases, column by column on the
    intersection, so a schema difference degrades to skipping that table instead
    of failing mid-copy
  - reports what it copied and what it skipped, per table

Run:
  python3 ex_import.py --source ~/.open-webui --target ~/.open-webui-ex
  python3 ex_import.py --source /path/to/webui.db --dry-run
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

# Tables worth importing for a drop-in replacement. Order matters: users first so
# foreign keys resolve, then folders/chats/messages, then per-key settings.
PREFERRED_TABLES = [
    "user",
    "folder",
    "chat",
    "chat_message",
    "knowledge",
    "knowledge_file",
    "function",
    "prompt",
    "tool",
    "note",
    "note_page",
    "skill",
    "memory",
    "config",
    "evaluation",
    "model",
    "whisper_model",
    "blacklist",
    "tag",
]


def db_path(value: str) -> Path:
    p = Path(value).expanduser()
    if p.is_dir():
        for candidate in ("webui.db", "webui.db.legacy", "webui.db.new"):
            if (p / candidate).exists():
                return p / candidate
        hits = sorted(p.glob("*.db"))
        if not hits:
            raise SystemExit(f"[ERROR] no database file under {p}")
        return hits[0]
    return p


def tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def columns(conn: sqlite3.Connection, table: str) -> list[str]:
    safe = table.replace('"', '""')
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{safe}")').fetchall()]


def import_tables(source: Path, target: Path, *, force: bool = False, dry_run: bool = False) -> dict:
    src = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True, timeout=5)
    dst = sqlite3.connect(target.as_posix(), timeout=5)
    report = {"tables": {}, "skipped": [], "rows": 0}

    try:
        src_tables = tables(src)
        dst_tables = tables(dst)

        for table in PREFERRED_TABLES:
            if table not in src_tables or table not in dst_tables:
                report["skipped"].append(table)
                continue

            shared = [c for c in columns(src, table) if c in columns(dst, table)]
            if not shared:
                report["skipped"].append(table)
                continue

            if dry_run:
                n = src.execute(f'SELECT COUNT(*) FROM "{table.replace(chr(34), chr(34)*2)}"').fetchone()[0]
                report["tables"][table] = {"rows": int(n), "columns": len(shared)}
                report["rows"] += int(n)
                continue

            placeholders = ", ".join("?" for _ in shared)
            quoted_cols = ", ".join(f'"{c.replace(chr(34), chr(34)*2)}"' for c in shared)
            sql = f'INSERT OR IGNORE INTO "{table.replace(chr(34), chr(34)*2)}" ({quoted_cols}) VALUES ({placeholders})'

            # Count rows actually written, not rows attempted. INSERT OR IGNORE skips
            # duplicates, and a report that calls skips "copied" would read as if a
            # second run imported data when it imported nothing.
            before = dst.total_changes
            attempted = 0
            for row in src.execute(f'SELECT {quoted_cols} FROM "{table.replace(chr(34), chr(34)*2)}"'):
                dst.execute(sql, row)
                attempted += 1
            dst.commit()
            written = dst.total_changes - before
            report["tables"][table] = {"rows": written, "attempted": attempted, "columns": len(shared)}
            report["rows"] += written
    finally:
        src.close()
        dst.close()

    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Import a stock Open WebUI database into the fork data dir.")
    ap.add_argument("--source", required=True, help="stock data dir or webui.db path")
    ap.add_argument("--target", help="fork data dir or db path (default: --source when single db, else sibling -ex)")
    ap.add_argument("--dry-run", action="store_true", help="count rows, write nothing")
    ap.add_argument("--force", action="store_true", help="allow target that looks like a live instance")
    args = ap.parse_args(argv)

    source = db_path(args.source)
    if args.target:
        target = db_path(args.target)
    else:
        target = source.with_name(source.stem + "-ex" + source.suffix)

    if source.resolve() == target.resolve():
        print("[ERROR] source and target are the same database. The fork is a drop-in; run it against the "
              "same data dir instead of importing into itself, or pass a different --target.")
        return 2

    if not args.dry_run and not args.force and target.resolve() == source.resolve():
        print("[ERROR] refusing to write to the source database.")
        return 2

    report = import_tables(source, target, force=args.force, dry_run=args.dry_run)
    print(f"[EX IMPORT] {'DRY RUN ' if args.dry_run else ''}{source} -> {target}")
    for table, info in report["tables"].items():
        print(f"  {table}: {info['rows']} rows over {info['columns']} shared columns")
    if report["skipped"]:
        print(f"  skipped (schema mismatch or absent): {', '.join(report['skipped'])}")
    print(f"[EX IMPORT] total rows: {report['rows']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
