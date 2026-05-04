"""Lazy loader for external-data-service modules."""
import sys, os
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_svc = os.path.join(_root, "services", "external-data-service")
if _svc not in sys.path:
    sys.path.insert(0, _svc)
if _root not in sys.path:
    sys.path.insert(0, _root)

def load_collector():
    from app.core.sentiment_collector import collect_all
    class _C:
        async def collect_all(self):
            return await collect_all()
    return _C()

def load_repo():
    from app.db.sentiment_repo import sentiment_repository
    return sentiment_repository

def load_scorer():
    try:
        from app.core.sentiment_scorer import sentiment_scorer
        return sentiment_scorer
    except Exception:
        return None
