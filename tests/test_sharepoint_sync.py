"""Unit tests for SharePoint sync helpers — Graph client mocked.

Real auth + uploads are tested manually via the dashboard (Admin → SharePoint
→ Test Connection / Push DB now). These tests cover the deterministic logic:
  - filename → source classification
  - WAL checkpoint behavior
  - backup ordering / retention
  - which-backup picking
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from datascrubb.sharepoint.sync import (
    apply_backup_retention,
    checkpoint_db,
    classify_source_files,
    download_source_files,
    list_db_backups,
    push_db_backup,
    restore_db_from_backup,
)


# ─── classify_source_files ──────────────────────────────────────────

def test_classify_recognizes_each_source():
    items = [
        {"name": "CRST data - 2026-Jan.xlsx"},
        {"name": "SAP_Jan thru_feb.xlsx"},
        {"name": "SAP report.xlsx"},
        {"name": "AI Troubleshooting_test.csv"},
        {"name": "fleet Troubleshooting log.csv"},
        {"name": "Backup 36673 of M3PL 01032026.xlsx"},
    ]
    out = classify_source_files(items)
    assert [i["name"] for i in out["crst"]] == ["CRST data - 2026-Jan.xlsx"]
    assert {i["name"] for i in out["sap"]} == {"SAP_Jan thru_feb.xlsx", "SAP report.xlsx"}
    assert {i["name"] for i in out["telemetry"]} == {
        "AI Troubleshooting_test.csv", "fleet Troubleshooting log.csv",
    }
    assert [i["name"] for i in out["m3pl"]] == ["Backup 36673 of M3PL 01032026.xlsx"]


def test_classify_skips_folders_and_unknown_files():
    items = [
        {"name": "SourcesArchive", "folder": {}},
        {"name": "random.txt"},
        {"name": "notes.docx"},
    ]
    out = classify_source_files(items)
    assert all(v == [] for v in out.values())


def test_classify_handles_empty_input():
    assert classify_source_files([]) == {"crst": [], "sap": [], "telemetry": [], "m3pl": []}


# ─── download_source_files ──────────────────────────────────────────

def test_download_returns_paths_grouped_by_source(tmp_path: Path):
    client = MagicMock()
    classified = {
        "crst": [{"id": "1", "name": "CRST.xlsx"}],
        "sap": [],
        "telemetry": [{"id": "2", "name": "tel.csv"}, {"id": "3", "name": "tel2.csv"}],
        "m3pl": [],
    }

    def fake_download(item, dest):
        Path(dest).write_bytes(b"fake")
        return Path(dest)

    client.download_file.side_effect = fake_download

    out = download_source_files(client, classified, tmp_path)

    assert set(out.keys()) == {"crst", "telemetry"}  # empty groups omitted
    assert [p.name for p in out["crst"]] == ["CRST.xlsx"]
    assert [p.name for p in out["telemetry"]] == ["tel.csv", "tel2.csv"]
    for p in out["crst"] + out["telemetry"]:
        assert p.exists()


# ─── checkpoint_db ──────────────────────────────────────────────────

def test_checkpoint_db_runs_truncating_pragma(tmp_path: Path):
    db = tmp_path / "test.db"
    con = sqlite3.connect(db)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE t (x INTEGER)")
    con.execute("INSERT INTO t VALUES (1), (2), (3)")
    con.commit()
    con.close()

    # Should not raise — and the call is idempotent
    checkpoint_db(db)
    checkpoint_db(db)


def test_checkpoint_db_skips_missing_file(tmp_path: Path):
    # Should not raise — log a warning and return
    checkpoint_db(tmp_path / "does_not_exist.db")


# ─── push_db_backup ─────────────────────────────────────────────────

def test_push_db_backup_uploads_with_run_id_in_name(tmp_path: Path):
    db = tmp_path / "datascrubb.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE t (x INTEGER)")
    con.commit()
    con.close()

    client = MagicMock()
    client.upload_file.return_value = {"id": "abc", "name": "datascrubb_run123.db"}

    item = push_db_backup(client, db, "Backups", run_id="run123")
    assert item["name"] == "datascrubb_run123.db"
    client.upload_file.assert_called_once()
    args, kwargs = client.upload_file.call_args
    assert args[0] == db
    assert args[1] == "Backups"
    assert kwargs["name"] == "datascrubb_run123.db"


def test_push_db_backup_raises_when_db_missing(tmp_path: Path):
    client = MagicMock()
    with pytest.raises(FileNotFoundError):
        push_db_backup(client, tmp_path / "nope.db", "Backups", run_id="x")


# ─── list_db_backups ────────────────────────────────────────────────

def test_list_db_backups_filters_and_sorts_newest_first():
    client = MagicMock()
    client.list_folder.return_value = [
        {"name": "datascrubb_2026-04-01.db", "lastModifiedDateTime": "2026-04-01T00:00:00Z"},
        {"name": "datascrubb_2026-05-01.db", "lastModifiedDateTime": "2026-05-01T00:00:00Z"},
        {"name": "datascrubb_2026-03-01.db", "lastModifiedDateTime": "2026-03-01T00:00:00Z"},
        {"name": "OldArchive", "folder": {}},
        {"name": "Trans_KPI_Validation_run_x.xlsx"},
        {"name": "datascrubb_typo.dbx"},  # wrong suffix, must skip
    ]
    out = list_db_backups(client, "Backups")
    assert [i["name"] for i in out] == [
        "datascrubb_2026-05-01.db",
        "datascrubb_2026-04-01.db",
        "datascrubb_2026-03-01.db",
    ]


# ─── apply_backup_retention ─────────────────────────────────────────

def test_apply_backup_retention_keeps_n_most_recent():
    client = MagicMock()
    backups = [
        {"id": f"id{i}", "name": f"datascrubb_{i:02d}.db",
         "lastModifiedDateTime": f"2026-05-{20 - i:02d}T00:00:00Z"}
        for i in range(5)  # 5 backups newest → oldest
    ]
    client.list_folder.return_value = backups

    deleted = apply_backup_retention(client, "Backups", keep_last_n=3)
    assert deleted == 2
    deleted_items = [c.args[0] for c in client.delete_item.call_args_list]
    assert {d["id"] for d in deleted_items} == {"id3", "id4"}


def test_apply_backup_retention_zero_keeps_all():
    client = MagicMock()
    client.list_folder.return_value = [
        {"id": "a", "name": "datascrubb_x.db", "lastModifiedDateTime": "2026-01-01T00:00:00Z"},
    ]
    assert apply_backup_retention(client, "Backups", keep_last_n=0) == 0
    client.delete_item.assert_not_called()


# ─── restore_db_from_backup ─────────────────────────────────────────

def test_restore_picks_latest_by_default(tmp_path: Path):
    backups = [
        {"id": "newer", "name": "datascrubb_b.db", "lastModifiedDateTime": "2026-05-02T00:00:00Z"},
        {"id": "older", "name": "datascrubb_a.db", "lastModifiedDateTime": "2026-05-01T00:00:00Z"},
    ]
    client = MagicMock()
    client.list_folder.return_value = backups

    def fake_download(item, dest):
        Path(dest).write_bytes(b"db-bytes-from-" + item["id"].encode())
        return Path(dest)

    client.download_file.side_effect = fake_download
    dest = tmp_path / "datascrubb.db"

    out = restore_db_from_backup(client, "Backups", dest, which="latest")
    assert out == dest
    assert dest.read_bytes() == b"db-bytes-from-newer"


def test_restore_picks_named_backup(tmp_path: Path):
    backups = [
        {"id": "newer", "name": "datascrubb_b.db", "lastModifiedDateTime": "2026-05-02T00:00:00Z"},
        {"id": "older", "name": "datascrubb_a.db", "lastModifiedDateTime": "2026-05-01T00:00:00Z"},
    ]
    client = MagicMock()
    client.list_folder.return_value = backups
    client.download_file.side_effect = lambda item, dest: Path(dest).write_bytes(b"x") or Path(dest)

    out = restore_db_from_backup(client, "Backups", tmp_path / "datascrubb.db", which="datascrubb_a.db")
    assert out.exists()
    # Verify the older one was downloaded
    args, _ = client.download_file.call_args
    assert args[0]["id"] == "older"


def test_restore_preserves_existing_db_as_prev(tmp_path: Path):
    dest = tmp_path / "datascrubb.db"
    dest.write_bytes(b"original-local-db")

    client = MagicMock()
    client.list_folder.return_value = [
        {"id": "x", "name": "datascrubb_only.db", "lastModifiedDateTime": "2026-05-02T00:00:00Z"},
    ]

    def fake_download(item, dst):
        Path(dst).write_bytes(b"restored-from-cloud")
        return Path(dst)

    client.download_file.side_effect = fake_download
    restore_db_from_backup(client, "Backups", dest, which="latest")
    assert dest.read_bytes() == b"restored-from-cloud"
    prev = dest.with_suffix(dest.suffix + ".prev")
    assert prev.exists()
    assert prev.read_bytes() == b"original-local-db"
