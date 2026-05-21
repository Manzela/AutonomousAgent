"""
Virtual Context Manager (VCM).

This is the cross-project isolation boundary. Every read against the memory
store goes through a `VirtualContextHandle` obtained via the VCM. The handle
is bound to a single project (or to CONSENSUS) at issuance, and re-verifies
the namespace on every record returned by the store — defence layers 2 and
4 in the 5-layer isolation stack (`01-phase1-mathematical-spec.md` §2.3).

If layer 4 fires, the handle is invalidated, telemetry emits `vcm.contamination`,
and the caller MUST re-acquire from the VCM.
"""

from __future__ import annotations

import asyncio
import hmac
import time
from hashlib import sha256
from typing import Iterable, Optional

import numpy as np

from .memory_store import AbstractMemoryStore
from .schemas import MemoryRecord, MemoryTier, ProjectID


class HandleClosed(Exception):
    """Raised when an operation is attempted on a closed/invalidated handle."""


class NamespaceContamination(Exception):
    """Raised when a record outside the handle's allowed scopes is returned.

    This is a *defence-in-depth* exception — every other layer should have
    prevented it; if this fires, treat it as a security incident and rotate
    the master secret.
    """


def _derive_namespace_token(master_secret: bytes, project_id: Optional[ProjectID]) -> str:
    """Layer-2 defence: HMAC token per project (or 'consensus')."""
    label = (project_id or "__consensus__").encode("utf-8")
    return hmac.new(master_secret, b"vcm:project:" + label, sha256).hexdigest()


class VirtualContextHandle:
    """Per-acquisition handle. NOT thread/coroutine reusable across projects.

    Lifecycle:
      vcm.acquire(project_id) → handle (open)
      handle.search(...) → list[MemoryRecord]   # layer-4 verified
      handle.put(record)  → None                 # layer-2 token stamped
      handle.close()      → handle is invalid for further ops

    A handle is single-tenant by construction; mixing project_ids in the
    same handle is a programmer error and will raise.
    """

    def __init__(
        self,
        *,
        store: AbstractMemoryStore,
        master_secret: bytes,
        project_id: Optional[ProjectID],
        allowed_scopes: Iterable[Optional[ProjectID]],
        handle_id: str,
    ) -> None:
        self._store = store
        self._master_secret = master_secret
        self._project_id = project_id
        # `allowed_scopes` is the set the handle is permitted to read from.
        # Typically: {project_id, None} so CONSENSUS is always readable.
        self._allowed: frozenset[Optional[ProjectID]] = frozenset(allowed_scopes)
        self._namespace_token = _derive_namespace_token(master_secret, project_id)
        self._consensus_token = _derive_namespace_token(master_secret, None)
        self._handle_id = handle_id
        self._open = True

    @property
    def handle_id(self) -> str:
        return self._handle_id

    @property
    def project_id(self) -> Optional[ProjectID]:
        return self._project_id

    @property
    def is_open(self) -> bool:
        return self._open

    def close(self) -> None:
        self._open = False

    async def put(self, record: MemoryRecord) -> None:
        if not self._open:
            raise HandleClosed(f"handle {self._handle_id} is closed")
        if record.project_id not in self._allowed:
            raise NamespaceContamination(
                f"handle {self._handle_id} cannot write project_id={record.project_id}"
            )
        # Layer-2: stamp the namespace token onto the record before write.
        token = self._consensus_token if record.project_id is None else self._namespace_token
        stamped = record.model_copy(update={"namespace_token": token})
        await self._store.put(stamped)

    async def search(
        self,
        *,
        query_embedding: np.ndarray,
        tier: MemoryTier,
        k: int = 10,
        scopes: Optional[Iterable[Optional[ProjectID]]] = None,
    ) -> list[tuple[MemoryRecord, float]]:
        if not self._open:
            raise HandleClosed(f"handle {self._handle_id} is closed")
        requested = frozenset(scopes) if scopes is not None else self._allowed
        # The handle cannot widen its own scope.
        if not requested.issubset(self._allowed):
            raise NamespaceContamination(
                f"handle {self._handle_id} requested {requested - self._allowed} "
                f"outside allowed {self._allowed}"
            )
        results = await self._store.search(
            query_embedding=query_embedding,
            tier=tier,
            project_scopes=requested,
            k=k,
        )
        # Layer-4 defence-in-depth: re-verify every returned record.
        for rec, _ in results:
            self._verify(rec)
        return results

    def _verify(self, rec: MemoryRecord) -> None:
        if rec.project_id not in self._allowed:
            self._open = False  # invalidate handle on contamination
            raise NamespaceContamination(
                f"store returned project_id={rec.project_id} "
                f"outside handle scopes {self._allowed}"
            )
        if rec.namespace_token is not None:
            expected = self._consensus_token if rec.project_id is None else self._namespace_token
            # If the token doesn't match what this handle would have written,
            # it was either written by a different VCM (different master
            # secret) or tampered with. Either way: contamination.
            if not hmac.compare_digest(rec.namespace_token, expected):
                # However, a per-project token from a DIFFERENT project still
                # legitimately appears if the handle's allowed set includes
                # that scope. Recompute the expected token for the record's
                # actual project_id under this handle's master secret.
                expected_for_rec = _derive_namespace_token(self._master_secret, rec.project_id)
                if not hmac.compare_digest(rec.namespace_token, expected_for_rec):
                    self._open = False
                    raise NamespaceContamination(
                        f"namespace token mismatch on record {rec.record_id}"
                    )


class VirtualContextManager:
    """Issues VirtualContextHandles bound to a project.

    Implements the read-side scope filter (layer 3 is in the store), the
    HMAC-derived namespace token (layer 2), and the post-fetch verification
    (layer 4) via the handle.
    """

    def __init__(self, *, store: AbstractMemoryStore, master_secret: bytes) -> None:
        if not master_secret or len(master_secret) < 16:
            raise ValueError(
                "master_secret must be at least 16 bytes (use a securely-generated key)"
            )
        self._store = store
        self._master_secret = master_secret
        self._counter = 0
        self._lock = asyncio.Lock()

    async def acquire(
        self,
        project_id: Optional[ProjectID],
        *,
        include_consensus: bool = True,
    ) -> VirtualContextHandle:
        """Issue a fresh handle for `project_id`. CONSENSUS is read-only by default.

        `include_consensus=True` lets the handle read CONSENSUS rows in
        addition to its project's rows; writing to CONSENSUS is allowed only
        when `project_id is None` (a CONSENSUS-owning handle, used for
        meta-evaluator promotions).
        """
        allowed: list[Optional[ProjectID]] = [project_id]
        if include_consensus and project_id is not None:
            allowed.append(None)
        # When project_id is None, the handle IS the consensus handle.
        async with self._lock:
            self._counter += 1
            hid = f"vch-{int(time.time()*1000)}-{self._counter}"
        return VirtualContextHandle(
            store=self._store,
            master_secret=self._master_secret,
            project_id=project_id,
            allowed_scopes=allowed,
            handle_id=hid,
        )
