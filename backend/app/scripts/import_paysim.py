"""Import PaySim CSV dataset into the transactions table.

Usage:
    python -m app.scripts.import_paysim --file "/path/to/paysim dataset.csv"
    python -m app.scripts.import_paysim --file "/path/to/paysim dataset.csv" --limit 50000
    # or via Docker:
    docker compose exec api python -m app.scripts.import_paysim --file "/data/paysim dataset.csv"
"""

import argparse
import asyncio
import csv
import json
import uuid
from datetime import datetime, timedelta, timezone

from app.core.database import async_session, engine
from app.models.base import Base
from app.models.transaction import RiskLevel, Transaction, TransactionType

BASE_DATE = datetime(2024, 1, 1, tzinfo=timezone.utc)

TYPE_MAP = {
    "CASH_IN": TransactionType.DEPOSIT,
    "CASH_OUT": TransactionType.WITHDRAWAL,
    "TRANSFER": TransactionType.TRANSFER,
    "PAYMENT": TransactionType.OTHER,
    "DEBIT": TransactionType.CHARGE,
}


def parse_row(row: dict) -> Transaction:
    tx_type = TYPE_MAP.get(row["type"], TransactionType.OTHER)
    is_fraud = int(row["isFraud"])
    is_flagged = int(row["isFlaggedFraud"])

    risk_level = None
    if is_flagged:
        risk_level = RiskLevel.HIGH
    elif is_fraud:
        risk_level = RiskLevel.MEDIUM
    else:
        risk_level = RiskLevel.LOW

    counterparty = row["nameDest"] if tx_type == TransactionType.TRANSFER else None

    raw = {
        "oldbalanceOrg": float(row["oldbalanceOrg"]),
        "newbalanceOrig": float(row["newbalanceOrig"]),
        "oldbalanceDest": float(row["oldbalanceDest"]),
        "newbalanceDest": float(row["newbalanceDest"]),
    }

    score_explanation = json.dumps({
        "ground_truth": is_fraud,
        "flagged_fraud": is_flagged,
    })

    return Transaction(
        fineract_transaction_id=f"PAYSIM-{uuid.uuid4().hex[:12].upper()}",
        fineract_account_id=row["nameOrig"],
        fineract_client_id=row["nameOrig"],
        transaction_type=tx_type,
        amount=float(row["amount"]),
        currency="USD",
        transaction_date=BASE_DATE + timedelta(hours=int(row["step"])),
        counterparty_account_id=counterparty,
        risk_level=risk_level,
        score_explanation=score_explanation,
        raw_payload=json.dumps(raw),
    )


async def import_paysim(filepath: str, limit: int | None = None, batch_size: int = 1000):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    total = 0
    skipped = 0

    async with async_session() as db:
        with open(filepath, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            batch = []

            for row in reader:
                if limit and total >= limit:
                    break

                try:
                    tx = parse_row(row)
                    batch.append(tx)
                    total += 1
                except Exception:
                    skipped += 1
                    continue

                if len(batch) >= batch_size:
                    db.add_all(batch)
                    await db.commit()
                    batch.clear()
                    print(f"  Imported {total} rows...", end="\r")

            if batch:
                db.add_all(batch)
                await db.commit()

    print(f"\nDone — {total} transactions imported, {skipped} skipped.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import PaySim CSV into fineract-aml database")
    parser.add_argument("--file", required=True, help="Path to the PaySim CSV file")
    parser.add_argument("--limit", type=int, default=None, help="Max rows to import (default: all)")
    parser.add_argument("--batch-size", type=int, default=1000, help="DB commit batch size (default: 1000)")
    args = parser.parse_args()

    asyncio.run(import_paysim(args.file, args.limit, args.batch_size))
