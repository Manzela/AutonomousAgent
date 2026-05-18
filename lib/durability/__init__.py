"""Durability plugin: failure-matrix-driven retry policy, checkpoint-resume (P1-3),
and REJECTED-inject (P1-4). P1-6 lands the real hook bodies here; P1-3 and P1-4
fill the on_session_start stubs in subsequent PRs."""

from lib.durability import failure_matrix, trichotomy, escalation, checkpoint, resume

__all__ = ["register", "failure_matrix", "trichotomy", "escalation", "checkpoint", "resume"]


def register(ctx):
    # P1-6 hooks (real implementations from this PR)
    ctx.register_hook("pre_tool_call", trichotomy.before_tool_call)
    ctx.register_hook("post_tool_call", trichotomy.after_tool_call)

    # P1-3 + P1-4 hooks (stubs; sessions c + d fill in)
    # ORDER MATTERS: resume must run first so REJECTED-inject can read active TaskSpec
    ctx.register_hook("on_session_start", _p1_3_resume_session)  # session-c fills
    ctx.register_hook("on_session_start", _p1_4_inject_rejected)  # session-d fills


def _p1_3_resume_session(ctx):
    """P1-3 (session-c): on container start, scan /data/checkpoints/ for incomplete
    sessions and rehydrate the latest checkpoint per session.

    Delegates to ``lib.durability.resume.rehydrate_latest_for_session`` which:
    - honours ``durability.checkpoint.autoresume_enabled`` in config/limits.yaml,
    - skips sessions marked DONE (via ``.done`` sentinel),
    - walks back from the highest-step file on corruption (skip_and_warn),
    - returns ``None`` when there's nothing to resume (the common case on a
      fresh box, where ``/data/checkpoints/`` does not exist).
    """
    return resume.rehydrate_latest_for_session(ctx)


def _p1_4_inject_rejected(ctx):
    """P1-4 (session-d): read active TaskSpec.intent_category, load matching unexpired
    REJECTED.md entries, inject as system message: 'Past failed approaches for this kind of
    task — DO NOT repeat:'. See ``lib.memory.rejected``.

    Local imports avoid a top-line import conflict with the P1-3 line that this
    session must not touch. The function never raises — any failure (no active
    spec, REJECTED.md missing, classifier down) silently no-ops so a memory
    fault can't block session start.
    """
    # Local imports — see docstring re: avoiding top-line conflict.
    from lib.memory import intent_classifier as _ic, rejected as _rej

    try:
        # Resolve the active TaskSpec from whatever ctx surface Hermes exposes.
        # Hermes' plugin contract here is not fully stable (P1-5/session-e will
        # firm it up); we defensively probe a couple of common shapes and
        # gracefully no-op when none is present.
        spec = getattr(ctx, "active_taskspec", None) or getattr(ctx, "taskspec", None)
        if spec is None and hasattr(ctx, "get_active_taskspec"):
            try:
                spec = ctx.get_active_taskspec()
            except Exception:  # noqa: BLE001
                spec = None
        if spec is None:
            return None

        # TaskSpec.intent_category is set at lock-time (P1-1). If absent,
        # classify on the fly using the cached classifier.
        category = getattr(spec, "intent_category", None) or "unknown"
        if category == "unknown" and hasattr(spec, "intent"):
            llm = getattr(ctx, "llm", None)
            if llm is not None:
                category = _ic.classify(
                    str(getattr(spec, "spec_id", "anon")),
                    str(getattr(spec, "intent", "")),
                    llm=llm,
                )

        # Read the per-session cap from limits.yaml; fall back to module default.
        max_inject = _rej.DEFAULT_MAX_INJECT
        try:
            import yaml  # local import; keeps the unit suite hermetic

            cfg_path = (
                __import__("pathlib").Path(__file__).resolve().parents[2] / "config" / "limits.yaml"
            )
            if cfg_path.exists():
                cfg = yaml.safe_load(cfg_path.read_text()) or {}
                max_inject = int(
                    (cfg.get("memory") or {}).get(
                        "rejected_max_inject_per_session", _rej.DEFAULT_MAX_INJECT
                    )
                )
        except Exception:  # noqa: BLE001
            pass

        entries = _rej.load_active_entries(intent_category=category, max_entries=max_inject)
        if not entries:
            return None

        body_lines = [
            "Past failed approaches for this kind of task — DO NOT repeat:",
            "",
        ]
        for e in entries:
            body_lines.append(f"- [{e.get('id', '?')}] {e.get('approach_summary', '')}")
            why = e.get("why_failed", "")
            if why:
                body_lines.append(f"  why_failed: {why}")
            alt = e.get("alternatives", "")
            if alt:
                body_lines.append(f"  alternatives: {alt}")
        message = "\n".join(body_lines)

        # ctx.inject_message is the documented Hermes contract; if it's absent
        # on the running build, log and return rather than crash.
        injector = getattr(ctx, "inject_message", None)
        if callable(injector):
            injector(role="system", content=message)
        else:
            import logging

            logging.getLogger(__name__).debug(
                "ctx.inject_message unavailable; skipping REJECTED inject (%d entries)",
                len(entries),
            )
        return None
    except Exception as exc:  # noqa: BLE001 — never block session start
        import logging

        logging.getLogger(__name__).warning("P1-4 REJECTED inject failed (non-fatal): %s", exc)
        return None
