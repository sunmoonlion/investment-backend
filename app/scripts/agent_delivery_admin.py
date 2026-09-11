"""Inspect/replay Agent dead letters, or run one reconciliation/delivery pass.

Use the backend service environment. Replay resets transport attempts only;
committed inbox acknowledgements still prevent executing completed commands.
"""

import argparse
import asyncio
import uuid

from sqlalchemy import text

from app.infrastructure.agent.delivery import AgentDelivery
from app.infrastructure.storage.postgres import get_postgres


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=["preflight", "dead-letters", "unknown-effects", "replay", "reconcile"],
    )
    parser.add_argument("--message-id", type=uuid.UUID)
    args = parser.parse_args()
    await get_postgres().init()
    sessions = get_postgres().session_factory
    delivery = AgentDelivery(sessions)
    try:
        if args.action == "preflight":
            # This query also runs before migration 0006. Never discard an old
            # accepted run whose input/approval is absent from the new journal.
            async with sessions() as s:
                result = await s.execute(
                    text("""
                    SELECT r.id, r.status FROM agent_runs r
                    WHERE r.status IN ('created','running','waiting')
                      AND NOT EXISTS(SELECT 1 FROM outbox_message m
                                     WHERE m.topic='agent.execution' AND m.aggregate_key=CAST(r.id AS text))
                """)
                )
                rows = result.all()
                for row in rows:
                    print(*row)
                if rows:
                    raise RuntimeError(
                        "drain or explicitly resolve legacy runs before switching workers"
                    )
                print("legacy run preflight passed")
        elif args.action == "unknown-effects":
            async with sessions() as s:
                rows = await s.execute(
                    text(
                        "select tool_call_id,run_id,status,updated_at from tool_side_effects where status in ('unknown','executing') order by updated_at"
                    )
                )
                for row in rows:
                    print(*row)
        elif args.action == "replay":
            if args.message_id is None:
                parser.error("replay requires --message-id")
            await delivery.replay(args.message_id)
        elif args.action == "reconcile":
            print(await delivery.reconcile())
        else:
            async with sessions() as s:
                rows = await s.execute(
                    text(
                        "select d.message_id,d.error_code,d.failed_at from outbox_dead_letter d "
                        "join outbox_message m on m.id=d.message_id "
                        "where d.replayed_at is null "
                        "and m.topic in ('agent.execution','agent.notification') order by d.failed_at"
                    )
                )
                for row in rows:
                    print(*row)
    finally:
        await get_postgres().shutdown()


if __name__ == "__main__":
    asyncio.run(main())
