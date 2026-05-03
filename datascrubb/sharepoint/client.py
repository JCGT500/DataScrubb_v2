"""Microsoft Graph API client for SharePoint sites.

Wraps the bits we actually need:
  - resolve a site URL → site_id
  - list / search a folder
  - download a file by path
  - upload a file (small ≤ 4 MB direct, large via createUploadSession)
  - delete a file by path

All paths are SharePoint drive paths relative to the site's default drive
(e.g. ``Shared Documents/DataScrubb/Sources``). The client converts those
to Graph URLs internally — callers pass folder/file paths as humans see them.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote, urlparse

import requests

from datascrubb.sharepoint.auth import acquire_token

logger = logging.getLogger("datascrubb.sharepoint.client")

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
SIMPLE_UPLOAD_LIMIT = 4 * 1024 * 1024  # Graph caps simple PUT at 4 MB
UPLOAD_CHUNK_SIZE = 5 * 1024 * 1024     # multiple of 320 KiB; balances throughput vs RAM


class GraphError(RuntimeError):
    """Raised when a Graph API call returns a non-2xx response."""


def _drive_path_segment(folder_path: str) -> str:
    """Encode a SharePoint drive path for a Graph URL.

    e.g. 'Shared Documents/DataScrubb/Sources' → 'Shared%20Documents/DataScrubb/Sources'.
    """
    return "/".join(quote(part, safe="") for part in folder_path.strip("/").split("/") if part)


class GraphClient:
    """Thin SharePoint client backed by Microsoft Graph + a delegated token."""

    def __init__(self, tenant_id: str, client_id: str, site_url: str):
        if not site_url:
            raise GraphError("site_url is required (e.g. https://contoso.sharepoint.com/sites/datascrubb)")
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.site_url = site_url.rstrip("/")
        self._site_id: str | None = None
        self._drive_id: str | None = None

    # ─── token / headers ────────────────────────────────────────────

    def _token(self) -> str:
        return acquire_token(self.tenant_id, self.client_id)

    def _headers(self, extra: dict | None = None) -> dict[str, str]:
        h = {"Authorization": f"Bearer {self._token()}"}
        if extra:
            h.update(extra)
        return h

    # ─── site / drive resolution ────────────────────────────────────

    def resolve_site(self) -> str:
        """Look up the SharePoint site_id from the site URL. Cached."""
        if self._site_id:
            return self._site_id
        parsed = urlparse(self.site_url)
        hostname = parsed.netloc
        site_path = parsed.path.lstrip("/")  # e.g. "sites/datascrubb"
        url = f"{GRAPH_BASE}/sites/{hostname}:/{site_path}" if site_path else f"{GRAPH_BASE}/sites/{hostname}"
        r = requests.get(url, headers=self._headers(), timeout=30)
        if not r.ok:
            raise GraphError(f"Failed to resolve site '{self.site_url}': {r.status_code} {r.text}")
        self._site_id = r.json()["id"]
        return self._site_id

    def resolve_drive(self) -> str:
        """Get the default document library drive_id. Cached."""
        if self._drive_id:
            return self._drive_id
        site_id = self.resolve_site()
        r = requests.get(f"{GRAPH_BASE}/sites/{site_id}/drive", headers=self._headers(), timeout=30)
        if not r.ok:
            raise GraphError(f"Failed to resolve drive: {r.status_code} {r.text}")
        self._drive_id = r.json()["id"]
        return self._drive_id

    # ─── folder operations ──────────────────────────────────────────

    def list_folder(self, folder_path: str) -> list[dict[str, Any]]:
        """List immediate children of a folder. Returns Graph driveItem dicts."""
        drive_id = self.resolve_drive()
        encoded = _drive_path_segment(folder_path)
        if encoded:
            url = f"{GRAPH_BASE}/drives/{drive_id}/root:/{encoded}:/children"
        else:
            url = f"{GRAPH_BASE}/drives/{drive_id}/root/children"
        items: list[dict[str, Any]] = []
        while url:
            r = requests.get(url, headers=self._headers(), timeout=60)
            if not r.ok:
                # 404 = folder doesn't exist; treat as empty and let caller decide
                if r.status_code == 404:
                    return []
                raise GraphError(f"Failed to list '{folder_path}': {r.status_code} {r.text}")
            data = r.json()
            items.extend(data.get("value", []))
            url = data.get("@odata.nextLink")
        return items

    def ensure_folder(self, folder_path: str) -> dict[str, Any]:
        """Create a folder (and intermediate folders) if it doesn't exist."""
        drive_id = self.resolve_drive()
        parts = [p for p in folder_path.strip("/").split("/") if p]
        # Walk path top-down, creating each missing segment
        cur = ""
        last_item: dict[str, Any] = {}
        for part in parts:
            parent = cur
            cur = f"{cur}/{part}".strip("/")
            # Try to GET; if 404, create
            encoded = _drive_path_segment(cur)
            r = requests.get(
                f"{GRAPH_BASE}/drives/{drive_id}/root:/{encoded}",
                headers=self._headers(), timeout=30,
            )
            if r.ok:
                last_item = r.json()
                continue
            if r.status_code != 404:
                raise GraphError(f"Folder check failed for '{cur}': {r.status_code} {r.text}")
            # Create it under parent
            if parent:
                create_url = f"{GRAPH_BASE}/drives/{drive_id}/root:/{_drive_path_segment(parent)}:/children"
            else:
                create_url = f"{GRAPH_BASE}/drives/{drive_id}/root/children"
            cr = requests.post(
                create_url,
                headers=self._headers({"Content-Type": "application/json"}),
                json={
                    "name": part,
                    "folder": {},
                    "@microsoft.graph.conflictBehavior": "fail",
                },
                timeout=30,
            )
            if not cr.ok:
                raise GraphError(f"Failed to create folder '{cur}': {cr.status_code} {cr.text}")
            last_item = cr.json()
        return last_item

    # ─── file operations ────────────────────────────────────────────

    def download_file(self, item_or_path: dict | str, dest: Path) -> Path:
        """Download a file by Graph driveItem dict OR by drive-relative path."""
        drive_id = self.resolve_drive()
        if isinstance(item_or_path, dict):
            item_id = item_or_path["id"]
            url = f"{GRAPH_BASE}/drives/{drive_id}/items/{item_id}/content"
        else:
            url = f"{GRAPH_BASE}/drives/{drive_id}/root:/{_drive_path_segment(item_or_path)}:/content"
        with requests.get(url, headers=self._headers(), stream=True, timeout=300) as r:
            if not r.ok:
                raise GraphError(f"Download failed: {r.status_code} {r.text}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        f.write(chunk)
        return dest

    def upload_file(self, src: Path, folder_path: str, name: str | None = None) -> dict[str, Any]:
        """Upload a local file to the SharePoint folder. Auto-picks simple
        vs resumable upload based on file size. Creates folder if missing.
        Returns the resulting Graph driveItem dict.
        """
        src = Path(src)
        if not src.exists():
            raise GraphError(f"Local file not found: {src}")
        name = name or src.name
        size = src.stat().st_size
        self.ensure_folder(folder_path)
        if size <= SIMPLE_UPLOAD_LIMIT:
            return self._upload_simple(src, folder_path, name)
        return self._upload_session(src, folder_path, name, size)

    def _upload_simple(self, src: Path, folder_path: str, name: str) -> dict[str, Any]:
        drive_id = self.resolve_drive()
        target = f"{folder_path.strip('/')}/{name}".strip("/")
        url = f"{GRAPH_BASE}/drives/{drive_id}/root:/{_drive_path_segment(target)}:/content"
        with open(src, "rb") as f:
            r = requests.put(
                url,
                headers=self._headers({"Content-Type": "application/octet-stream"}),
                data=f.read(),
                timeout=300,
            )
        if not r.ok:
            raise GraphError(f"Simple upload failed: {r.status_code} {r.text}")
        return r.json()

    def _upload_session(self, src: Path, folder_path: str, name: str, size: int) -> dict[str, Any]:
        drive_id = self.resolve_drive()
        target = f"{folder_path.strip('/')}/{name}".strip("/")
        create_url = f"{GRAPH_BASE}/drives/{drive_id}/root:/{_drive_path_segment(target)}:/createUploadSession"
        sr = requests.post(
            create_url,
            headers=self._headers({"Content-Type": "application/json"}),
            json={"item": {"@microsoft.graph.conflictBehavior": "replace", "name": name}},
            timeout=60,
        )
        if not sr.ok:
            raise GraphError(f"createUploadSession failed: {sr.status_code} {sr.text}")
        upload_url = sr.json()["uploadUrl"]
        offset = 0
        with open(src, "rb") as f:
            while offset < size:
                chunk = f.read(UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                end = offset + len(chunk) - 1
                headers = {
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {offset}-{end}/{size}",
                }
                cr = requests.put(upload_url, headers=headers, data=chunk, timeout=300)
                # 200/201 = final chunk done, 202 = more to go
                if cr.status_code not in (200, 201, 202):
                    raise GraphError(f"Chunk upload failed at byte {offset}: {cr.status_code} {cr.text}")
                offset += len(chunk)
                if cr.status_code in (200, 201):
                    return cr.json()
        # Loop should have returned on the final chunk
        raise GraphError("Upload session ended without a final 200/201 response")

    def delete_item(self, item_or_path: dict | str) -> None:
        drive_id = self.resolve_drive()
        if isinstance(item_or_path, dict):
            url = f"{GRAPH_BASE}/drives/{drive_id}/items/{item_or_path['id']}"
        else:
            url = f"{GRAPH_BASE}/drives/{drive_id}/root:/{_drive_path_segment(item_or_path)}"
        r = requests.delete(url, headers=self._headers(), timeout=60)
        if not r.ok and r.status_code != 404:
            raise GraphError(f"Delete failed: {r.status_code} {r.text}")

    # ─── convenience ────────────────────────────────────────────────

    def whoami(self) -> dict[str, Any]:
        """Return the signed-in user profile (for 'Signed in as ...' display)."""
        r = requests.get(f"{GRAPH_BASE}/me", headers=self._headers(), timeout=30)
        if not r.ok:
            raise GraphError(f"/me failed: {r.status_code} {r.text}")
        return r.json()
