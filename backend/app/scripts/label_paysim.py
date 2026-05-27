"""Create Alert + Review records from imported PaySim transactions.

Reads the ground-truth isFraud / isFlaggedFraud values stored in
score_explanation during import and produces labeled training data
for XGBoost — identical in shape to what seed_labels.py generates.

Only processes PAYSIM-* transactions that have no alert yet.

Usage:
    docker compose exec api python -m app.scripts.label_paysim
    docker compose exec api python -m app.scripts.label_paysim --limit 5000
    docker compose exec api python -m app.scripts.label_paysim --dry-run
"""

import argparse
import asyncio
import json

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.database import async_session
from app.models.alert import Alert, AlertSource, AlertStatus
from app.models.review import Review, ReviewDecision
from app.models.transaction import Transaction
from app.models.user import User

BATCH_SIZE = 500


async def label_paysim(limit: int | None, dry_run: bool) -> None:
    async with async_session() as db:
        result = await db.execute(select(User).where(User.username == "admin"))
        admin = result.scalar_one_or_none()
        if not admin:
            print("ERROR: admin user not found. Run seed_data.py first.")
            return

        # Fetch PAYSIM transactions that have no alert yet
        already_alerted = select(Alert.transaction_id)
        q = (
            select(Transaction)
            .where(
                Transaction.fineract_transaction_id.like("PAYSIM-%"),
                Transaction.id.not_in(already_alerted),
            )
            .options(selectinload(Transaction.alerts))
            .order_by(Transaction.transaction_date)
        )
        if limit:
            q = q.limit(limit)

        rows = (await db.execute(q)).scalars().all()

    if not rows:
        print("No unlabeled PAYSIM transactions found.")
        return

    print(f"Found {len(rows)} unlabeled PAYSIM transactions.")
    if dry_run:
        fraud = sum(
            1 for tx in rows
            if json.loads(tx.score_explanation or "{}").get("ground_truth", 0)
        )
        print(f"  Fraud: {fraud}  |  Legitimate: {len(rows) - fraud}  (dry-run, nothing written)")
        return

    fraud_count = 0
    legit_count = 0
    batch_num = 0

    async with async_session() as db:
        result = await db.execute(select(User).where(User.username == "admin"))
        admin = result.scalar_one_or_none()

        for i, tx in enumerate(rows, 1):
            meta = json.loads(tx.score_explanation or "{}")
            is_fraud = bool(meta.get("ground_truth", 0))
            is_flagged = bool(meta.get("flagged_fraud", 0))

            if is_fraud or is_flagged:
                alert_status = AlertStatus.CONFIRMED_FRAUD
                decision = ReviewDecision.CONFIRMED_FRAUD
                notes = "PaySim ground-truth: isFraud=1"
                if is_flagged:
                    notes += ", isFlaggedFraud=1"
                risk_score = tx.risk_score if tx.risk_score is not None else 0.85
                fraud_count += 1
            else:
                alert_status = AlertStatus.FALSE_POSITIVE
                decision = ReviewDecision.LEGITIMATE
                notes = "PaySim ground-truth: isFraud=0"
                risk_score = tx.risk_score if tx.risk_score is not None else 0.15
                legit_count += 1

            alert = Alert(
                transaction_id=tx.id,
                risk_score=risk_score,
                status=alert_status,
                source=AlertSource.ML_MODEL,
                title="PaySim labeled transaction",
                description="Ground-truth label from PaySim dataset",
            )
            db.add(alert)
            await db.flush()

            review = Review(
                alert_id=alert.id,
                reviewer_id=admin.id,
                decision=decision,
                notes=notes,
            )
            db.add(review)

            if i % BATCH_SIZE == 0:
                await db.commit()
                batch_num += 1
                print(f"  Committed {i}/{len(rows)} labels...", end="\r")

        await db.commit()

    print(
        f"\nDone — {fraud_count} fraud + {legit_count} legitimate labels created "
        f"({fraud_count + legit_count} total)."
    )
    print()
    print("Now trigger XGBoost retraining:")
    print(
        '  docker compose run --rm -e PYTHONPATH=/app api python -c '
        '"from app.tasks.training import retrain_fraud_classifier; retrain_fraud_classifier()"'
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Label imported PaySim transactions for XGBoost training")
    parser.add_argument("--limit", type=int, default=None, help="Max transactions to label (default: all)")
    parser.add_argument("--dry-run", action="store_true", help="Count without writing anything")
    args = parser.parse_args()

    asyncio.run(label_paysim(args.limit, args.dry_run))
