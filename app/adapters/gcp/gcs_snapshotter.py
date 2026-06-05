"""GCS Workspace Snapshotter — GCP/GCS concretion of workspace filesystem snapshotting (SP-R7)."""

from __future__ import annotations

import hashlib
import logging
import os
import tarfile
import tempfile
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class GcsWorkspaceSnapshotter:
    """GcsWorkspaceSnapshotter implementation for SP-R7."""

    def __init__(
        self,
        bucket_name: Optional[str] = None,
        kms_key_name: Optional[str] = None,
    ) -> None:
        self.bucket_name = bucket_name or os.environ.get(
            "SPINE_SNAPSHOT_BUCKET", "autonomous-agent-snapshots"
        )
        self.kms_key_name = kms_key_name or os.environ.get("SPINE_SNAPSHOT_KMS_KEY")

    def _gcs_client(self) -> Any:
        from google.cloud import storage

        return storage.Client()

    def snapshot(self, workdir: Path, thread_id: str, node_id: str) -> Optional[dict[str, Any]]:
        """Archive the workspace directory (excluding .git) and upload to GCS."""
        if not workdir or not workdir.exists():
            logger.warning("GcsWorkspaceSnapshotter.snapshot: workdir missing: %s", workdir)
            return None

        # Build GCS object path: workspaces/{thread_id}/{node_id}.tar.gz
        object_name = f"workspaces/{thread_id}/{node_id}.tar.gz"

        tmp_tar_path = None
        try:
            # Create temporary archive
            tmp_fd, tmp_tar_path = tempfile.mkstemp(suffix=".tar.gz", prefix="aa-ws-snap-")
            os.close(tmp_fd)

            # Tar filter to exclude the .git file/directory
            def tar_filter(tarinfo: tarfile.TarInfo) -> Optional[tarfile.TarInfo]:
                if tarinfo.name == ".git" or tarinfo.name.endswith("/.git"):
                    return None
                return tarinfo

            with tarfile.open(tmp_tar_path, "w:gz") as tar:
                # Add all files from workdir
                tar.add(str(workdir), arcname="", filter=tar_filter)

            # Compute content address digest
            # We can hash the generated tarball to get a content address
            hasher = hashlib.sha256()
            with open(tmp_tar_path, "rb") as f:
                while chunk := f.read(8192):
                    hasher.update(chunk)
            digest = hasher.hexdigest()

            # Upload to GCS
            client = self._gcs_client()
            bucket = client.bucket(self.bucket_name)

            # CMEK encryption parameters support
            blob = bucket.blob(object_name, kms_key_name=self.kms_key_name)
            blob.upload_from_filename(tmp_tar_path, content_type="application/gzip")

            logger.info(
                "GcsWorkspaceSnapshotter: uploaded gs://%s/%s digest=%s",
                self.bucket_name,
                object_name,
                digest,
            )

            return {
                "kind": "gcs",
                "bucket": self.bucket_name,
                "object": object_name,
                "digest": digest,
                "ref": f"gs://{self.bucket_name}/{object_name}",
            }
        except Exception as exc:
            logger.warning("GcsWorkspaceSnapshotter.snapshot failed: %s", exc)
            return None
        finally:
            if tmp_tar_path and os.path.exists(tmp_tar_path):
                try:
                    os.unlink(tmp_tar_path)
                except OSError:
                    pass

    def rehydrate(self, target_dir: Path, thread_id: str, node_id: str) -> bool:
        """Download the archive from GCS and extract it to target_dir."""
        if not target_dir or not target_dir.exists():
            logger.warning("GcsWorkspaceSnapshotter.rehydrate: target_dir missing: %s", target_dir)
            return False

        object_name = f"workspaces/{thread_id}/{node_id}.tar.gz"

        tmp_tar_path = None
        try:
            # Download to a temporary file first
            tmp_fd, tmp_tar_path = tempfile.mkstemp(suffix=".tar.gz", prefix="aa-ws-rehydrate-")
            os.close(tmp_fd)

            client = self._gcs_client()
            bucket = client.bucket(self.bucket_name)
            blob = bucket.blob(object_name)
            blob.download_to_filename(tmp_tar_path)

            # Extract archive to target_dir (skipping .git)
            with tarfile.open(tmp_tar_path, "r:gz") as tar:
                # Use extractall securely, excluding any malicious path traversals or .git
                members = []
                for member in tar.getmembers():
                    # Sanity check: no path traversal
                    resolved_path = (target_dir / member.name).resolve()
                    if not str(resolved_path).startswith(str(target_dir.resolve())):
                        continue
                    if member.name == ".git" or member.name.startswith(".git/"):
                        continue
                    members.append(member)
                tar.extractall(path=str(target_dir), members=members)

            logger.info("GcsWorkspaceSnapshotter: successfully rehydrated to %s", target_dir)
            return True
        except Exception as exc:
            logger.warning("GcsWorkspaceSnapshotter.rehydrate failed: %s", exc)
            return False
        finally:
            if tmp_tar_path and os.path.exists(tmp_tar_path):
                try:
                    os.unlink(tmp_tar_path)
                except OSError:
                    pass
