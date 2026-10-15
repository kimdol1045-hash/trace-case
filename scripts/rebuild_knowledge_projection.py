"""Preview or apply the safe PostgreSQL knowledge projection rebuild.

Preview is the default and performs no writes. Apply requires the exact
confirmation phrase, the event-order digest printed by a fresh preview, and a
dotted factory that returns the same configured KnowledgeOutboxConsumer used
for ordinary incremental processing.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
from collections.abc import Callable
from typing import cast

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tc_backend.config import load_settings
from tc_backend.knowledge.rebuild import (
    REBUILD_CONFIRMATION,
    KnowledgeProjectionRebuilder,
)
from tc_backend.worker.knowledge_consumer import KnowledgeOutboxConsumer

ConsumerFactory = Callable[[sessionmaker[Session]], KnowledgeOutboxConsumer]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Preview or atomically rebuild only the PostgreSQL knowledge projection."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="apply the reset/replay; omitted means read-only preview",
    )
    parser.add_argument(
        "--confirm",
        help=f"apply guard; must exactly equal {REBUILD_CONFIRMATION}",
    )
    parser.add_argument(
        "--expected-event-order-digest",
        help="eventOrderDigest from the immediately preceding preview",
    )
    parser.add_argument(
        "--expected-source-ledger-digest",
        help="sourceLedgerDigest from the immediately preceding preview",
    )
    parser.add_argument(
        "--consumer-factory",
        help=(
            "dotted module:callable accepting a SQLAlchemy sessionmaker and returning "
            "the configured incremental KnowledgeOutboxConsumer"
        ),
    )
    return parser


def _load_consumer_factory(value: str) -> ConsumerFactory:
    module_name, separator, attribute_name = value.partition(":")
    if not separator or not module_name or not attribute_name:
        raise ValueError("consumer factory must use module.path:callable syntax")
    module = importlib.import_module(module_name)
    candidate = getattr(module, attribute_name)
    if not callable(candidate):
        raise TypeError("consumer factory attribute is not callable")
    return cast(ConsumerFactory, candidate)


async def _run(args: argparse.Namespace) -> dict[str, object]:
    settings = load_settings()
    engine = create_engine(settings.sqlalchemy_database_url, pool_pre_ping=True)
    sessions = sessionmaker(engine, expire_on_commit=False)
    try:
        if not args.apply:
            return KnowledgeProjectionRebuilder(session_factory=sessions).plan().as_dict()
        factory = _load_consumer_factory(str(args.consumer_factory))
        consumer = factory(sessions)
        if not isinstance(consumer, KnowledgeOutboxConsumer):
            raise TypeError("consumer factory did not return KnowledgeOutboxConsumer")
        rebuilder = KnowledgeProjectionRebuilder(
            session_factory=sessions,
            consumer=consumer,
        )
        report = await rebuilder.apply(
            confirmation=str(args.confirm),
            expected_event_order_digest=str(args.expected_event_order_digest),
            expected_source_ledger_digest=str(args.expected_source_ledger_digest),
        )
        return report.as_dict()
    finally:
        engine.dispose()


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    if args.apply:
        missing = [
            option
            for option, value in (
                ("--confirm", args.confirm),
                ("--expected-event-order-digest", args.expected_event_order_digest),
                ("--expected-source-ledger-digest", args.expected_source_ledger_digest),
                ("--consumer-factory", args.consumer_factory),
            )
            if not value
        ]
        if missing:
            parser.error(f"--apply requires {', '.join(missing)}")
        if args.confirm != REBUILD_CONFIRMATION:
            parser.error(f"--confirm must exactly equal {REBUILD_CONFIRMATION}")
    elif (
        args.confirm
        or args.expected_event_order_digest
        or args.expected_source_ledger_digest
        or args.consumer_factory
    ):
        parser.error("apply-only options require --apply")
    print(json.dumps(asyncio.run(_run(args)), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
