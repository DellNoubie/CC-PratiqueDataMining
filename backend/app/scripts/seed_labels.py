"""Generate labeled transactions (fraud + legitimate) to train XGBoost.

Creates:
  - 800 legitimate transactions with reviews
  - 250 fraud transactions with reviews
  Total: 1050 labeled samples → satisfies MIN_FRAUD_SAMPLES=200, MIN_TOTAL_SAMPLES=1000

Usage:
    docker compose run --rm -e PYTHONPATH=/app api python app/scripts/seed_labels.py
"""

import asyncio
import random
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.database import async_session
from app.models.alert import Alert, AlertSource, AlertStatus
from app.models.review import Review, ReviewDecision
from app.models.transaction import RiskLevel, Transaction, TransactionType
from app.models.user import User


async def seed_labels():
    rng = random.Random(99)
    now = datetime.now(timezone.utc)

    accounts = [f"ACC-{i:03d}" for i in range(1, 21)]
    clients = [f"CLI-{i:03d}" for i in range(1, 16)]
    sample_ips = [
        "192.168.1.100", "10.0.0.50", "172.16.0.10",
        "41.202.207.14", "41.210.45.67",
        "197.239.5.100", "154.72.166.30",
        "8.8.8.8", "185.220.101.1",
        None,
    ]

    async with async_session() as db:
        # Get admin user to be the reviewer
        result = await db.execute(select(User).where(User.username == "admin"))
        admin = result.scalar_one_or_none()
        if not admin:
            print("ERROR: admin user not found. Run seed_data.py first.")
            return

        legit_count = 0
        fraud_count = 0

        # ── 800 LEGITIMATE transactions ──────────────────────────────────────
        for i in range(800):
            # Normal patterns: daytime, small-medium amounts, common types
            hour = rng.randint(8, 18)
            tx_date = now - timedelta(hours=rng.uniform(1, 720))
            tx_date = tx_date.replace(hour=hour)

            amount = round(rng.lognormvariate(5.5, 1.2), 2)
            amount = max(100, min(amount, 8000))  # Keep below structuring threshold

            tx_type = rng.choice([
                TransactionType.DEPOSIT,
                TransactionType.WITHDRAWAL,
                TransactionType.TRANSFER,
            ])

            tx = Transaction(
                fineract_transaction_id=f"LBL-LEG-{uuid.uuid4().hex[:10].upper()}",
                fineract_account_id=rng.choice(accounts),
                fineract_client_id=rng.choice(clients),
                transaction_type=tx_type,
                amount=amount,
                currency="XAF",
                transaction_date=tx_date,
                counterparty_account_id=rng.choice(accounts) if tx_type == TransactionType.TRANSFER else None,
                description=rng.choice(["Salary", "Utility bill", "Mobile payment", "ATM withdrawal", "Transfer"]),
                ip_address=rng.choice(sample_ips[:6]),  # Known IPs only
                country_code="CM",
                risk_score=rng.uniform(0.1, 0.45),
                anomaly_score=rng.uniform(0.0, 0.4),
                risk_level=RiskLevel.LOW,
            )
            db.add(tx)
            await db.flush()

            # Create alert
            alert = Alert(
                transaction_id=tx.id,
                risk_score=tx.risk_score,
                status=AlertStatus.FALSE_POSITIVE,
                source=AlertSource.RULE_ENGINE,
                title="Suspicious transaction flagged",
                description="Auto-generated for training data",
            )
            db.add(alert)
            await db.flush()

            # Create review: LEGITIMATE
            review = Review(
                alert_id=alert.id,
                reviewer_id=admin.id,
                decision=ReviewDecision.LEGITIMATE,
                notes="Normal transaction pattern",
            )
            db.add(review)
            legit_count += 1

        # ── 250 FRAUD transactions ────────────────────────────────────────────
        fraud_patterns = [
            # Structuring: amounts just below 10k threshold
            lambda: {
                "amount": rng.uniform(9500, 9990),
                "hour": rng.randint(2, 5),
                "tx_type": TransactionType.WITHDRAWAL,
                "risk_score": rng.uniform(0.75, 0.95),
                "notes": "Structuring pattern detected",
            },
            # Large transfer at night
            lambda: {
                "amount": rng.uniform(15000, 80000),
                "hour": rng.randint(0, 4),
                "tx_type": TransactionType.TRANSFER,
                "risk_score": rng.uniform(0.70, 0.92),
                "notes": "Large night transfer",
            },
            # Round number withdrawal
            lambda: {
                "amount": rng.choice([10000, 20000, 50000, 100000]),
                "hour": rng.randint(2, 6),
                "tx_type": TransactionType.WITHDRAWAL,
                "risk_score": rng.uniform(0.65, 0.88),
                "notes": "Suspicious round amount at unusual hour",
            },
            # High amount transfer to new account
            lambda: {
                "amount": rng.uniform(50000, 200000),
                "hour": rng.randint(1, 5),
                "tx_type": TransactionType.TRANSFER,
                "risk_score": rng.uniform(0.80, 0.99),
                "notes": "High value transfer at night to new counterparty",
            },
        ]

        for i in range(250):
            pattern = rng.choice(fraud_patterns)()
            tx_date = now - timedelta(hours=rng.uniform(1, 720))
            tx_date = tx_date.replace(hour=pattern["hour"])

            tx = Transaction(
                fineract_transaction_id=f"LBL-FRD-{uuid.uuid4().hex[:10].upper()}",
                fineract_account_id=rng.choice(accounts),
                fineract_client_id=rng.choice(clients),
                transaction_type=pattern["tx_type"],
                amount=round(pattern["amount"], 2),
                currency="XAF",
                transaction_date=tx_date,
                counterparty_account_id=rng.choice(accounts) if pattern["tx_type"] == TransactionType.TRANSFER else None,
                description=rng.choice(["Transfer", "Cash withdrawal", None]),
                ip_address=rng.choice(sample_ips[6:]),  # Suspicious IPs
                country_code=rng.choice(["CM", "NG", "US", None]),
                risk_score=pattern["risk_score"],
                anomaly_score=rng.uniform(0.6, 0.95),
                risk_level=RiskLevel.CRITICAL,
            )
            db.add(tx)
            await db.flush()

            # Create alert
            alert = Alert(
                transaction_id=tx.id,
                risk_score=tx.risk_score,
                status=AlertStatus.CONFIRMED_FRAUD,
                source=AlertSource.RULE_ENGINE,
                title="High risk transaction detected",
                description="Auto-generated for training data",
            )
            db.add(alert)
            await db.flush()

            # Create review: CONFIRMED_FRAUD
            review = Review(
                alert_id=alert.id,
                reviewer_id=admin.id,
                decision=ReviewDecision.CONFIRMED_FRAUD,
                notes=pattern["notes"],
            )
            db.add(review)
            fraud_count += 1

        await db.commit()
        print(f"Created {legit_count} legitimate labeled transactions")
        print(f"Created {fraud_count} fraud labeled transactions")
        print(f"Total labels: {legit_count + fraud_count}")
        print()
        print("Now trigger XGBoost training:")
        print('  docker compose run --rm -e PYTHONPATH=/app api python -c "from app.tasks.training import retrain_fraud_classifier; retrain_fraud_classifier()"')


if __name__ == "__main__":
    asyncio.run(seed_labels())
