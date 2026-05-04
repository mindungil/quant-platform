from fastapi import APIRouter

from app.core.snapshot import build_external_context
from app.models.external_data import ExternalContextSnapshot

router = APIRouter()


@router.get("/external/context/{asset}", response_model=ExternalContextSnapshot)
def get_external_context(asset: str) -> ExternalContextSnapshot:
    return build_external_context(asset)


@router.get("/external/context/{asset}/status")
def get_external_context_status(asset: str) -> dict:
    snapshot = build_external_context(asset)
    return {
        "asset": snapshot.asset,
        "timestamp": snapshot.timestamp,
        "source_timestamp": snapshot.source_timestamp,
        "degraded_mode": snapshot.degraded_mode,
        "stale": snapshot.stale,
        "source": snapshot.source,
        "missing_fields": snapshot.missing_fields,
    }


@router.get("/external/news/{asset}")
async def get_news_sentiment(asset: str):
    from app.core.sentiment import compute_news_sentiment
    return await compute_news_sentiment(asset)


@router.get("/external/kimchi-premium/{asset}")
def get_kimchi_premium(asset: str = "BTC"):
    """Get current kimchi premium for an asset (Upbit KRW vs Binance USDT)."""
    from shared.factors.kimchi_premium import compute_kimchi_premium
    return compute_kimchi_premium(asset)
