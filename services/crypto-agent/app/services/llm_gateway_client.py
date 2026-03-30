import httpx


class LlmGatewayClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    def generate_reasoning(
        self,
        *,
        asset: str,
        signal_score: float,
        strategy_name: str,
        memory_count: int,
        components: dict[str, float],
    ) -> str:
        response = httpx.post(
            f"{self._base_url}/reasoning/generate",
            json={
                "asset": asset,
                "signal_score": signal_score,
                "strategy_name": strategy_name,
                "memory_count": memory_count,
                "components": components,
            },
            timeout=5.0,
        )
        response.raise_for_status()
        payload = response.json()
        return payload["reasoning"]
