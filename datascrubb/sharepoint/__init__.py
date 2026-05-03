"""SharePoint integration: auth, Microsoft Graph client, and sync helpers.

Pulls source data files from a SharePoint folder and pushes the SQLite DB
(and Excel exports) back as backups after each pipeline run.
"""

from datascrubb.sharepoint.auth import (
    SharepointAuthError,
    acquire_token,
    initiate_device_flow,
    sign_out,
    signed_in_account,
)
from datascrubb.sharepoint.client import (
    GraphClient,
    GraphError,
)
from datascrubb.sharepoint.sync import (
    checkpoint_db,
    classify_source_files,
    download_source_files,
    list_source_files,
    push_db_backup,
    push_excel_backup,
    restore_db_from_backup,
    list_db_backups,
    apply_backup_retention,
)

__all__ = [
    "SharepointAuthError",
    "acquire_token",
    "initiate_device_flow",
    "sign_out",
    "signed_in_account",
    "GraphClient",
    "GraphError",
    "checkpoint_db",
    "classify_source_files",
    "download_source_files",
    "list_source_files",
    "push_db_backup",
    "push_excel_backup",
    "restore_db_from_backup",
    "list_db_backups",
    "apply_backup_retention",
]
