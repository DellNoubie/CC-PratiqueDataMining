import asyncio
import json
from sqlalchemy import select
from app.core.config import settings
from app.core.database import async_session
from app.models.transaction import Transaction, RiskLevel


def _risk_level_from_score(score: float) -> RiskLevel:
    if score >= settings.risk_score_high:
        return RiskLevel.HIGH
    if score >= settings.risk_score_medium:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


async def fix():
    async with async_session() as db:
        result = await db.execute(
            select(Transaction).where(
                Transaction.fineract_transaction_id.like("PAYSIM-%")
            )
        )
        txs = result.scalars().all()
        fraud = 0
        for tx in txs:
            exp = json.loads(tx.score_explanation) if tx.score_explanation else {}

            if "final_score" in exp:
                # Already ML-analyzed — restore from final_score
                score = float(exp["final_score"])
            elif "ground_truth" in exp:
                # New import with ground truth label
                score = 1.0 if exp["ground_truth"] == 1 else 0.05
            else:
                score = 0.05

            tx.risk_score = score
            tx.risk_level = _risk_level_from_score(score)
            if score >= 0.8:
                fraud += 1

        await db.commit()
        print(f"Updated {len(txs)} transactions ({fraud} high-risk, {len(txs)-fraud} low-risk)")


asyncio.run(fix())
