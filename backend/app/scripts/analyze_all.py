"""Submit all unanalyzed transactions to the Celery analysis pipeline.

Usage:
    docker compose exec api python -m app.scripts.analyze_all
    docker compose exec api python -m app.scripts.analyze_all --limit 1000
"""

import argparse
import asyncio

from sqlalchemy import select

from app.core.database import async_session
from app.models.transaction import Transaction
from app.tasks.analysis import analyze_transaction


async def get_unanalyzed_ids(limit: int | None) -> list[str]:
    async with async_session() as db:
        q = select(Transaction.id).where(
            Transaction.risk_score.is_(None),
            ~Transaction.fineract_transaction_id.like("PAYSIM-%"),
        )
        if limit:
            q = q.limit(limit)
        result = await db.execute(q)
        return [str(row[0]) for row in result.fetchall()]


def main(limit: int | None):
    ids = asyncio.run(get_unanalyzed_ids(limit))
    if not ids:
        print("No unanalyzed transactions found.")
        return

    print(f"Submitting {len(ids)} transactions to Celery...")
    for i, tx_id in enumerate(ids, 1):
        analyze_transaction.delay(tx_id)
        if i % 1000 == 0:
            print(f"  Queued {i}/{len(ids)}...")

    print(f"Done — {len(ids)} tasks submitted to Celery worker.")
    print("Monitor progress: docker compose logs -f celery-worker")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Max transactions to analyze")
    args = parser.parse_args()
    main(args.limit)
