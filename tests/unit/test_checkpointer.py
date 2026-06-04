"""SP-01 checkpointer provider seam (the 7th sibling ABC).

AbstractCheckpointer is a thin PROVIDER (build_saver -> BaseCheckpointSaver +
durability_mode), NOT a re-declaration of langgraph's checkpointer protocol
(BaseCheckpointSaver is already a rich ABC; re-implementing it is the §12
over-engineering). The graph does compile(checkpointer=provider.build_saver()).
"""

from __future__ import annotations

import pytest

from langgraph.checkpoint.base import BaseCheckpointSaver

from app.adapters.inmemory.checkpointer import InMemoryCheckpointer
from app.core.checkpointer import AbstractCheckpointer


def test_abstract_checkpointer_cannot_instantiate():
    with pytest.raises(TypeError):
        AbstractCheckpointer()  # ABC with abstract build_saver / durability_mode


def test_inmemory_build_saver_returns_basecheckpointsaver():
    cp = InMemoryCheckpointer()
    saver = cp.build_saver()
    assert isinstance(saver, BaseCheckpointSaver)


def test_inmemory_build_saver_is_stable_instance():
    """The same provider yields the SAME saver instance — so a fresh compile()
    sharing this provider resumes the prior checkpoint (the crash stand-in)."""
    cp = InMemoryCheckpointer()
    assert cp.build_saver() is cp.build_saver()


def test_inmemory_durability_mode_is_async_for_ci():
    assert InMemoryCheckpointer().durability_mode == "async"


async def test_setup_and_aclose_are_noops():
    cp = InMemoryCheckpointer()
    await cp.setup()
    await cp.aclose()  # must not raise
