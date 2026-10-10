"""Recompute every knowledge base's ``chunk_count`` from Dify's segment totals.

The card's 「分块 N」 accumulates a document's ``total_segments`` when it reaches
``completed`` and gives them back on delete, so documents that completed before
that accumulator shipped are missing from the total. This rebuilds each total as
the sum over the KB's locally tracked ``completed`` documents, and snapshots
every previous value so a run can be undone with ``--rollback <snapshot_id>``.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import async_session_factory
from app.dify_console import DifyConsoleClient, DifyConsoleError
from app.logging_conf import configure_logging, get_logger
from app.models.knowledge import (
    KbChunkCountSnapshot,
    KnowledgeBaseRegistry,
    KnowledgeDocument,
)

log = get_logger(__name__)

_EXIT_OK = 0
# Finished, but some documents could not be read — the operator has to look.
_EXIT_INCOMPLETE = 1
_EXIT_USAGE = 2

# A Dify error body can be a whole HTML/JSON page; the failure list stays
# readable at one clipped line per document.
_MAX_ERROR_CHARS = 200

_HTTP_NOT_FOUND = 404


@dataclass(frozen=True)
class KnowledgeBaseRef:
    """One KB plus the documents its ``chunk_count`` should be the sum of."""

    kb_id: str
    name: str
    dify_dataset_id: str
    chunk_count: int
    document_ids: tuple[str, ...]


@dataclass(frozen=True)
class Recompute:
    """The total recomputed for one KB, alongside the value it replaces."""

    kb: KnowledgeBaseRef
    suggested_chunk_count: int

    @property
    def has_changed(self) -> bool:
        return self.suggested_chunk_count != self.kb.chunk_count


@dataclass(frozen=True)
class DocumentFailure:
    """A document whose segment count could not be read, and why."""

    kb_id: str
    document_id: str
    error: str


@dataclass(frozen=True)
class MissingDocument:
    """A locally completed document the upstream no longer holds (404).

    It contributes nothing to its KB's total — the upstream delete semantics
    treat an absent document as already gone — so one stale row cannot keep the
    KB from converging; the line is still reported for cleanup.
    """

    kb_id: str
    document_id: str


@dataclass
class Plan:
    """What a run would rewrite, and the documents that blocked it."""

    changes: list[Recompute] = field(default_factory=list)
    failures: list[DocumentFailure] = field(default_factory=list)
    missing: list[MissingDocument] = field(default_factory=list)
    skipped_kbs: int = 0


async def _load_knowledge_bases(session: AsyncSession) -> list[KnowledgeBaseRef]:
    """Read every KB and the ``completed`` documents that feed its total.

    Two queries rather than two per KB: the sum's input is exactly the set the
    accumulator adds to and the delete path gives back — locally recorded
    ``completed`` rows, not Dify's live status.
    """
    bases = (
        await session.scalars(select(KnowledgeBaseRegistry).order_by(KnowledgeBaseRegistry.kb_id))
    ).all()
    documents = (
        await session.scalars(
            select(KnowledgeDocument)
            .where(KnowledgeDocument.status == "completed")
            .order_by(KnowledgeDocument.kb_id, KnowledgeDocument.document_id)
        )
    ).all()

    by_kb: dict[str, list[str]] = {}
    for document in documents:
        by_kb.setdefault(document.kb_id, []).append(document.document_id)

    return [
        KnowledgeBaseRef(
            kb_id=base.kb_id,
            name=base.name,
            dify_dataset_id=base.dify_dataset_id,
            chunk_count=base.chunk_count,
            document_ids=tuple(by_kb.get(base.kb_id, ())),
        )
        for base in bases
    ]


async def _document_segments(
    client: DifyConsoleClient, kb: KnowledgeBaseRef, document_id: str
) -> int:
    """``total_segments`` Dify holds for a document; 0 when it reports none.

    Same quantity and same fallback as the indexing poll, so a recomputed total
    keeps the meaning the accumulator gives a fresh increment.
    """
    status = await client.get_document_indexing_status(kb.dify_dataset_id, document_id)
    return int(status.get("total_segments") or 0)


async def _plan_recompute(client: DifyConsoleClient, kbs: Sequence[KnowledgeBaseRef]) -> Plan:
    """Sum each KB's document segments, leaving KBs with unreadable ones alone.

    A partial sum would silently undercount — the defect this script repairs —
    so one unreadable document takes its whole KB out of the run, and only that
    KB. The remaining documents are still probed, so the report carries the
    whole failure surface instead of its first line. A document the upstream no
    longer holds (404) is not a failure: it contributes nothing and its KB
    still converges.
    """
    plan = Plan()
    for kb in kbs:
        total = 0
        blocked = False
        for document_id in kb.document_ids:
            try:
                total += await _document_segments(client, kb, document_id)
            except (DifyConsoleError, httpx.HTTPError) as exc:
                if isinstance(exc, DifyConsoleError) and exc.status_code == _HTTP_NOT_FOUND:
                    plan.missing.append(MissingDocument(kb_id=kb.kb_id, document_id=document_id))
                    continue
                plan.failures.append(_document_failure(kb, document_id, exc))
                blocked = True
        if blocked:
            plan.skipped_kbs += 1
            continue
        plan.changes.append(Recompute(kb=kb, suggested_chunk_count=total))
    return plan


def _document_failure(kb: KnowledgeBaseRef, document_id: str, exc: Exception) -> DocumentFailure:
    return DocumentFailure(kb_id=kb.kb_id, document_id=document_id, error=_error_text(exc))


async def _apply(changes: Sequence[Recompute], *, snapshot_id: str) -> tuple[int, list[str]]:
    """Overwrite each changed KB's total under a compare-and-set, one transaction.

    The update only fires while the stored value still equals the one the plan
    read, so an increment the API committed in between (``add_chunk_count``)
    survives instead of being overwritten; its KB is reported as stale and left
    for the next run. A snapshot row is written only where the value was
    actually overwritten, so ``--rollback`` never restores a value that was
    never applied.
    """
    written = 0
    stale: list[str] = []
    async with async_session_factory() as session:
        for change in changes:
            result = await session.execute(
                update(KnowledgeBaseRegistry)
                .where(
                    KnowledgeBaseRegistry.kb_id == change.kb.kb_id,
                    KnowledgeBaseRegistry.chunk_count == change.kb.chunk_count,
                )
                .values(chunk_count=change.suggested_chunk_count)
                .returning(KnowledgeBaseRegistry.kb_id)
            )
            if not result.scalars().all():
                stale.append(change.kb.kb_id)
                continue
            written += 1
            session.add(
                KbChunkCountSnapshot(
                    snapshot_id=snapshot_id,
                    kb_id=change.kb.kb_id,
                    previous_chunk_count=change.kb.chunk_count,
                )
            )
        await session.commit()
    return written, stale


async def _restore(snapshot_id: str) -> int | None:
    """Put back the totals one snapshot recorded; ``None`` when the id is unknown.

    Restores unconditionally: the snapshot holds the pre-run value, so a
    rollback after further indexing overwrites those increments too — re-run
    the recompute afterwards to converge again.
    """
    async with async_session_factory() as session:
        rows = (
            await session.scalars(
                select(KbChunkCountSnapshot).where(KbChunkCountSnapshot.snapshot_id == snapshot_id)
            )
        ).all()
        if not rows:
            print(f"unknown snapshot_id: {snapshot_id}", file=sys.stderr)
            return None
        for row in rows:
            await session.execute(
                update(KnowledgeBaseRegistry)
                .where(KnowledgeBaseRegistry.kb_id == row.kb_id)
                .values(chunk_count=row.previous_chunk_count)
            )
        await session.commit()
    return len(rows)


async def _recompute(settings: Settings, *, dry_run: bool) -> int:
    """Recompute every KB's total; returns the process exit code."""
    client = DifyConsoleClient(settings)
    await client.startup()
    try:
        try:
            # Fail fast: without a session every document read fails the same way.
            await client.login()
        except (DifyConsoleError, httpx.HTTPError) as exc:
            print(f"cannot reach the Dify console: {_error_text(exc)}", file=sys.stderr)
            return _EXIT_USAGE

        started = time.monotonic()
        async with async_session_factory() as session:
            kbs = await _load_knowledge_bases(session)
        plan = await _plan_recompute(client, kbs)
        changes = [change for change in plan.changes if change.has_changed]

        _print_diff_table(changes)
        snapshot_id = ""
        rows_written = 0
        stale: list[str] = []
        if dry_run:
            print(f"dry run: {len(changes)} of {len(kbs)} KB(s) would change; nothing written")
        elif not changes:
            print("applied: 0 KB(s) changed; nothing written")
        else:
            snapshot_id = _new_snapshot_id()
            rows_written, stale = await _apply(changes, snapshot_id=snapshot_id)
            print(f"applied: {rows_written} KB row(s), snapshot_id={snapshot_id}")
        _print_document_notes(plan.failures, plan.missing)
        for kb_id in stale:
            print(
                f"stale kb={kb_id}: counter changed while the run read it, left for the next run",
                file=sys.stderr,
            )

        elapsed = time.monotonic() - started
        print(
            f"summary: kbs={len(kbs)} changed_kbs={len(changes)} changed_rows={rows_written} "
            f"skipped_kbs={plan.skipped_kbs} stale_kbs={len(stale)} "
            f"missing_documents={len(plan.missing)} failed_documents={len(plan.failures)} "
            f"elapsed={elapsed:.1f}s"
        )
        log.info(
            "kb_chunk_count_recompute_completed",
            resource="knowledge",
            kbs=len(kbs),
            changed_kbs=len(changes),
            changed_rows=rows_written,
            skipped_kbs=plan.skipped_kbs,
            stale_kbs=len(stale),
            missing_documents=len(plan.missing),
            failed_documents=len(plan.failures),
            snapshot_id=snapshot_id or None,
            dry_run=dry_run,
            duration_ms=round(elapsed * 1000, 1),
        )
        return _EXIT_INCOMPLETE if (plan.failures or stale) else _EXIT_OK
    finally:
        await client.shutdown()


async def _rollback_run(snapshot_id: str) -> int:
    """Restore one snapshot's totals; returns the process exit code."""
    started = time.monotonic()
    restored = await _restore(snapshot_id)
    if restored is None:
        return _EXIT_USAGE
    elapsed = time.monotonic() - started
    print(f"restored: rows={restored} snapshot_id={snapshot_id} elapsed={elapsed:.1f}s")
    log.info(
        "kb_chunk_count_rollback_completed",
        resource="knowledge",
        rows=restored,
        snapshot_id=snapshot_id,
        duration_ms=round(elapsed * 1000, 1),
    )
    return _EXIT_OK


def _print_diff_table(changes: Sequence[Recompute]) -> None:
    """Print one line per KB whose total moves: id, name, documents, then, now."""
    if not changes:
        return
    columns = ("kb_id", "name", "documents", "current", "suggested")
    rows = [
        (
            change.kb.kb_id,
            change.kb.name,
            str(len(change.kb.document_ids)),
            str(change.kb.chunk_count),
            str(change.suggested_chunk_count),
        )
        for change in changes
    ]
    widths = [max(len(columns[i]), *(len(row[i]) for row in rows)) for i in range(len(columns))]
    for row in (columns, *rows):
        print("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)))


def _print_document_notes(
    failures: Sequence[DocumentFailure], missing: Sequence[MissingDocument]
) -> None:
    """List unreadable and gone-upstream documents on stderr.

    Stdout stays a pure diff report: only the counters and the diff table are
    meant to be piped, the per-document detail is for the operator's eyes.
    """
    for failure in failures:
        print(
            f"failed kb={failure.kb_id} document={failure.document_id}: {failure.error}",
            file=sys.stderr,
        )
    for document in missing:
        print(
            f"missing kb={document.kb_id} document={document.document_id}: "
            "gone upstream, counted as 0",
            file=sys.stderr,
        )


def _new_snapshot_id() -> str:
    """Timestamped run handle, e.g. ``20261010T081500Z-3f9a1c2b``."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


def _error_text(exc: Exception) -> str:
    """One clipped line of error text; Dify bodies can carry newlines and pages."""
    detail = " ".join(str(exc).split())
    return f"{type(exc).__name__}: {detail[:_MAX_ERROR_CHARS]}"


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="recompute_kb_counts",
        description="Recompute knowledge-base chunk totals from Dify's live segment counts.",
        epilog=(
            "Run as `python -m scripts.recompute_kb_counts` from apps/backend (or /app in the "
            "backend container) with DATABASE_URL and the Dify console credentials configured. "
            "A KB whose documents cannot all be read from Dify is skipped, never partially "
            "applied."
        ),
    )
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument(
        "--dry-run",
        action="store_true",
        help="list the KBs that would change and write nothing",
    )
    actions.add_argument(
        "--rollback",
        metavar="SNAPSHOT_ID",
        help="restore the totals an earlier run overwrote",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    if args.rollback:
        return asyncio.run(_rollback_run(args.rollback))
    return asyncio.run(_recompute(settings, dry_run=args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
