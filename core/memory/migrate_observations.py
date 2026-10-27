"""One-off: bring stored passive observations into slice 1's shape (issue #265).

Before slice 1 the ingestor stored every device dropping off the network as an
observation, and wrote no time into the text. This deletes each stored observation
whose transition touches ``unavailable`` or ``unknown`` — the ingestor rendered a
missing state as ``unknown`` too — and stamps the rest with their local time the way
the ingestor writes them now, re-embedding the text. The semantic key is time-free and
stays, with its vector.

    docker exec <alfred-container> python -m core.memory.migrate_observations [--apply]

A dry run unless ``--apply``. Safe to run again: stamped entries are left alone. The
time is the entry's own timestamp, which for these entries is when the observation was
built, seconds after the event.
"""

from __future__ import annotations

import argparse
import asyncio
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import batched
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from core.memory.ingestor import local_stamp
from core.memory.paths import episodic_cold_path
from core.memory.redis_vector_store import RedisVectorStore
from core.memory.sqlite_vec_store import SqliteVecStore
from core.reflex.availability import UNAVAILABLE_STATES
from core.shutdown import teardown
from shared.config import AlfredConfig
from shared.redis_streams import create_redis
from shared.usertime import get_user_timezone

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from core.memory.embedding_provider import EmbeddingProvider
    from core.memory.vector_store import SearchResult, VectorStore

# The ingestor's own tags: passive observations, and the actions Reflex took.
_TAGGED = re.compile(r"^(?P<tag>\[(?:observation|reflex:[^\]]+)\]) (?P<body>.*)$", re.DOTALL)
# What local_stamp writes after the tag, joined to the rest by " — ".
_STAMP = re.compile(r"^[A-Z][a-z]{2} \d{4}-\d{2}-\d{2} \d{2}:\d{2} — ")
# ``entity: old → new``, then the salient attributes in parentheses, if any.
_TRANSITION = re.compile(r"^\S+: (?P<old>.*?) → (?P<new>.*?)(?: \(.*\))?$", re.DOTALL)

BATCH_SIZE = 64


@dataclass(frozen=True)
class Rewrite:
    id: str
    before: str
    after: str


@dataclass
class Plan:
    blips: list[SearchResult] = field(default_factory=list)
    rewrites: list[Rewrite] = field(default_factory=list)
    # Observations with no timestamp to stamp them with, left as they are.
    undated: list[SearchResult] = field(default_factory=list)
    # Rewrites whose entry had left the store by the time they were written.
    gone: int = 0


def _is_blip(tag: str, body: str) -> bool:
    if tag != "[observation]":
        return False
    transition = _TRANSITION.match(body)
    return transition is not None and (
        transition["old"] in UNAVAILABLE_STATES or transition["new"] in UNAVAILABLE_STATES
    )


def plan_migration(entries: Iterable[SearchResult], tz: ZoneInfo) -> Plan:
    plan = Plan()
    for entry in entries:
        tagged = _TAGGED.match(entry.content)
        if tagged is None:
            continue
        tag, body = tagged["tag"], tagged["body"]
        stamp = _STAMP.match(body)
        if _is_blip(tag, body[stamp.end() :] if stamp else body):
            plan.blips.append(entry)
        elif stamp:
            continue
        elif entry.metadata.timestamp <= 0:
            plan.undated.append(entry)
        else:
            at = datetime.fromtimestamp(entry.metadata.timestamp, UTC)
            after = f"{tag} {local_stamp(at, tz)} — {body}"
            plan.rewrites.append(Rewrite(id=entry.id, before=entry.content, after=after))
    return plan


async def migrate(
    store: VectorStore,
    embedder: EmbeddingProvider,
    tz: ZoneInfo,
    *,
    apply: bool,
    batch_size: int = BATCH_SIZE,
) -> Plan:
    """Plan one store's migration, and carry it out when ``apply`` is set."""
    plan = plan_migration(await store.select({}), tz)
    if not apply:
        return plan
    for blip in plan.blips:
        await store.delete(blip.id)
    for batch in batched(plan.rewrites, batch_size, strict=False):
        vectors = await embedder.embed_batch([rewrite.after for rewrite in batch])
        for rewrite, vector in zip(batch, vectors, strict=True):
            if not await store.replace_content(rewrite.id, rewrite.after, vector):
                plan.gone += 1
    return plan


def _samples(entries: Sequence[str], limit: int) -> list[str]:
    return [f"    {text}" for text in entries[:limit]]


def describe(name: str, plan: Plan, samples: int = 5) -> str:
    lines = [
        f"{name}: {len(plan.blips)} blips to delete, {len(plan.rewrites)} to stamp,"
        f" {len(plan.undated)} without a time"
        + (f", {plan.gone} gone before their rewrite" if plan.gone else "")
    ]
    lines += ["  delete:", *_samples([b.content for b in plan.blips], samples)]
    lines += ["  stamp:", *_samples([r.after for r in plan.rewrites], samples)]
    lines += ["  no time:", *_samples([u.content for u in plan.undated], samples)]
    return "\n".join(lines)


async def run(config: AlfredConfig, *, apply: bool) -> None:
    redis = create_redis(config.redis_url)
    embedder: EmbeddingProvider | None = None
    cold: SqliteVecStore | None = None
    try:
        from core.memory.embedding_backend import build_embedding_provider

        embedder = build_embedding_provider(config)
        tz = ZoneInfo(await get_user_timezone(redis))
        hot = RedisVectorStore(redis=redis, dim=config.embedding_dim)
        cold = SqliteVecStore(db_path=str(episodic_cold_path()), dim=config.embedding_dim)
        # Hot first: an entry the decay pass moves to cold meanwhile is then still
        # found by the cold store's own select.
        for name, store in (("hot", hot), ("cold", cold)):
            plan = await migrate(store, embedder, tz, apply=apply)
            print(describe(name, plan))
        print("Applied." if apply else "Dry run — nothing written. Re-run with --apply.")
    finally:
        await teardown(
            closers={
                "embedding provider": embedder.aclose if embedder is not None else None,
                "cold store": cold.close if cold is not None else None,
                "redis": redis.aclose,
            },
        )


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="write the changes")
    args = parser.parse_args(argv)
    asyncio.run(run(AlfredConfig.from_env(), apply=args.apply))


if __name__ == "__main__":
    main()
