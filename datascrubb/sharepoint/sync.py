"""High-level SharePoint sync helpers: source-file pull + DB backup push.

The classification mirrors ``run_pipeline.py`` so the same filename
patterns work for both local globbing and SharePoint folder listing.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from datascrubb.sharepoint.client import GraphClient, GraphError

logger = logging.getLogger("datascrubb.sharepoint.sync")

# Filename patterns by source — keep aligned with run_pipeline.py:29-44.
SOURCE_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "crst": [re.compile(r"^CRST data.*\.xlsx?$", re.IGNORECASE)],
    "sap": [
        re.compile(r"^SAP_.*\.xlsx?$", re.IGNORECASE),
        re.compile(r"^SAP.*\.xlsx?$", re.IGNORECASE),
    ],
    "telemetry": [
        re.compile(r"^AI Troubleshooting.*\.csv$", re.IGNORECASE),
        re.compile(r".*Troubleshooting.*\.csv$", re.IGNORECASE),
    ],
    "m3pl": [re.compile(r"^Backup .*M3PL.*\.xlsx?$", re.IGNORECASE)],
}

DB_BACKUP_PREFIX = "datascrubb_"
DB_BACKUP_SUFFIX = ".db"
EXCEL_BACKUP_PREFIX = "Trans_KPI_Validation_"


def classify_source_files(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Classify Graph driveItem dicts by source. Skips folders & unknown files."""
    out: dict[str, list[dict[str, Any]]] = {k: [] for k in SOURCE_PATTERNS}
    for item in items:
        if "folder" in item:
            continue
        name = item.get("name", "")
        for source, patterns in SOURCE_PATTERNS.items():
            if any(p.match(name) for p in patterns):
                out[source].append(item)
                break
    return out


def list_source_files(client: GraphClient, source_folder: str) -> dict[str, list[dict[str, Any]]]:
    """List & classify all files in the SharePoint source folder."""
    items = client.list_folder(source_folder)
    return classify_source_files(items)


def download_source_files(
    client: GraphClient,
    classified: dict[str, list[dict[str, Any]]],
    dest_dir: Path,
) -> dict[str, list[Path]]:
    """Download every classified file into dest_dir, preserving original names.

    Returns the ``{source: [Path, ...]}`` dict that ``Pipeline.run()`` accepts.
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, list[Path]] = {}
    for source, items in classified.items():
        if not items:
            continue
        out[source] = []
        for item in items:
            local = dest_dir / item["name"]
            client.download_file(item, local)
            logger.info("Downloaded %s → %s (%d bytes)", item["name"], local, local.stat().st_size)
            out[source].append(local)
    return out


# ─── DB sync ─────────────────────────────────────────────────────────

def checkpoint_db(db_path: Path) -> None:
    """Force WAL → main DB merge so the .db file is a complete snapshot.

    Without this, an upload of just ``datascrubb.db`` would miss any
    in-flight writes still sitting in ``datascrubb.db-wal``.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        logger.warning("checkpoint_db: %s does not exist; skipping", db_path)
        return
    con = sqlite3.connect(db_path)
    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.commit()
    finally:
        con.close()


def push_db_backup(
    client: GraphClient,
    db_path: Path,
    backup_folder: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Checkpoint + upload the SQLite DB. Returns the Graph item dict."""
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"DB not found at {db_path}")
    checkpoint_db(db_path)
    suffix = run_id or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_utc")
    name = f"{DB_BACKUP_PREFIX}{suffix}{DB_BACKUP_SUFFIX}"
    item = client.upload_file(db_path, backup_folder, name=name)
    logger.info("Pushed DB backup to SharePoint: %s/%s (%d bytes)",
                backup_folder, name, db_path.stat().st_size)
    return item


def push_excel_backup(
    client: GraphClient,
    excel_path: Path,
    backup_folder: str,
) -> dict[str, Any]:
    """Upload an Excel export — keeps its original filename for traceability."""
    excel_path = Path(excel_path)
    if not excel_path.exists():
        raise FileNotFoundError(f"Excel export not found at {excel_path}")
    item = client.upload_file(excel_path, backup_folder, name=excel_path.name)
    logger.info("Pushed Excel backup to SharePoint: %s/%s", backup_folder, excel_path.name)
    return item


def list_db_backups(client: GraphClient, backup_folder: str) -> list[dict[str, Any]]:
    """List all DB backups in the folder, sorted newest → oldest."""
    items = client.list_folder(backup_folder)
    db_items = [
        i for i in items
        if "folder" not in i
        and i.get("name", "").startswith(DB_BACKUP_PREFIX)
        and i.get("name", "").endswith(DB_BACKUP_SUFFIX)
    ]
    db_items.sort(key=lambda i: i.get("lastModifiedDateTime", ""), reverse=True)
    return db_items


def restore_db_from_backup(
    client: GraphClient,
    backup_folder: str,
    dest: Path,
    which: str = "latest",
) -> Path:
    """Download a DB backup over the local DB.

    ``which`` = "latest" picks the newest by lastModifiedDateTime; otherwise
    treat as a filename and pick the matching backup exactly.
    """
    dest = Path(dest)
    backups = list_db_backups(client, backup_folder)
    if not backups:
        raise GraphError(f"No DB backups found in {backup_folder}")
    if which == "latest":
        item = backups[0]
    else:
        match = [i for i in backups if i.get("name") == which]
        if not match:
            raise GraphError(f"Backup '{which}' not found in {backup_folder}")
        item = match[0]
    # Download to a sibling .new file then atomically replace
    tmp = dest.with_suffix(dest.suffix + ".new")
    client.download_file(item, tmp)
    if dest.exists():
        backup_local = dest.with_suffix(dest.suffix + ".prev")
        if backup_local.exists():
            backup_local.unlink()
        dest.replace(backup_local)
    tmp.replace(dest)
    logger.info("Restored DB from %s/%s → %s", backup_folder, item["name"], dest)
    return dest


def apply_backup_retention(
    client: GraphClient,
    backup_folder: str,
    keep_last_n: int,
) -> int:
    """Delete DB backups older than the most recent N. 0 = keep all.
    Returns the number of backups deleted.
    """
    if keep_last_n <= 0:
        return 0
    backups = list_db_backups(client, backup_folder)
    to_delete = backups[keep_last_n:]
    for item in to_delete:
        try:
            client.delete_item(item)
            logger.info("Retention: deleted old backup %s", item.get("name"))
        except GraphError as e:
            logger.warning("Retention: failed to delete %s: %s", item.get("name"), e)
    return len(to_delete)
