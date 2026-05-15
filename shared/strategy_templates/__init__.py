"""Korean market strategy templates — testimonial-style descriptions."""

KOREAN_TEMPLATES = [
    {
        "id": "btc_dca_low_buy",
        "name": "비트코인 저점 매수",
        "category": "보수",
        "description": "RSI가 30 이하일 때만 매수, 50 도달 시 부분 매도. 변동성 큰 장에서 안전한 분할 매수 전략.",
        "testimonial": "급락장에 불안한 마음 없이 자동으로 모아갑니다",
        "asset_type": "crypto",
        "assets": ["BTCUSDT", "KRW-BTC"],
        "factors": ["rsi_extreme", "bb_contrarian", "fear_greed"],
        "weights": {"rsi_extreme": 1.5, "bb_contrarian": 1.0, "fear_greed": 1.2},
        "risk_level": "low",
        "expected_monthly_return": "3-8%",
    },
    {
        "id": "kimchi_arbitrage",
        "name": "김치 프리미엄 차익거래",
        "category": "시장중립",
        "description": "한국과 글로벌 가격 차이를 추적하여 프리미엄 발생 시 매도, 디스카운트 시 매수.",
        "testimonial": "한국 시장에만 있는 기회를 놓치지 마세요",
        "asset_type": "crypto",
        "assets": ["BTCUSDT", "ETHUSDT"],
        "factors": ["kimchi_premium", "fear_greed"],
        "weights": {"kimchi_premium": 2.0, "fear_greed": 0.5},
        "risk_level": "medium",
        "expected_monthly_return": "5-12%",
    },
    {
        "id": "eth_rsi_trend",
        "name": "이더리움 RSI 추세 추종",
        "category": "공격",
        "description": "이더리움 RSI와 MACD 동시 매수 시그널에서 진입, 추세 약화 시 청산.",
        "testimonial": "이더리움 변동성을 활용한 단기 수익 전략",
        "asset_type": "crypto",
        "assets": ["ETHUSDT"],
        "factors": ["rsi_level", "macd_histogram", "ema_cross_9_21"],
        "weights": {"rsi_level": 1.2, "macd_histogram": 1.5, "ema_cross_9_21": 1.3},
        "risk_level": "high",
        "expected_monthly_return": "8-20%",
    },
    {
        "id": "fear_greed_contrarian",
        "name": "공포지수 역발상 매매",
        "category": "보수",
        "description": "Fear & Greed 지수가 극단적 공포(15 이하) 시 매수, 극단적 탐욕(85 이상) 시 매도.",
        "testimonial": "남들이 두려워할 때 매수하는 워런 버핏 전략",
        "asset_type": "crypto",
        "assets": ["BTCUSDT", "ETHUSDT"],
        "factors": ["fear_greed", "macro_risk"],
        "weights": {"fear_greed": 2.0, "macro_risk": 1.0},
        "risk_level": "low",
        "expected_monthly_return": "4-10%",
    },
    {
        "id": "weekend_volatility",
        "name": "주말 변동성 활용",
        "category": "공격",
        "description": "주말에 변동성이 커지는 시점을 노려 짧은 진입/청산. 24시간 봇이 자동 처리.",
        "testimonial": "잠자는 동안에도 시장 변동을 활용",
        "asset_type": "crypto",
        "assets": ["BTCUSDT", "SOLUSDT"],
        "factors": ["atr_relative", "bb_width", "volume_volatility"],
        "weights": {"atr_relative": 1.5, "bb_width": 1.2, "volume_volatility": 1.0},
        "risk_level": "high",
        "expected_monthly_return": "10-25%",
    },
    {
        "id": "momentum_breakout",
        "name": "돌파 모멘텀 추적",
        "category": "공격",
        "description": "볼린저 밴드 상단 돌파 + 거래량 급증 시 진입, ATR 트레일링 스톱.",
        "testimonial": "큰 움직임의 시작을 잡아내는 전략",
        "asset_type": "crypto",
        "assets": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        "factors": ["bollinger_pctb", "macd_crossover", "volume_volatility"],
        "weights": {"bollinger_pctb": 1.5, "macd_crossover": 1.3, "volume_volatility": 1.5},
        "risk_level": "high",
        "expected_monthly_return": "8-18%",
    },
    {
        "id": "stable_dca_eth",
        "name": "이더리움 정기 매수 (DCA)",
        "category": "보수",
        "description": "매일 일정 금액 매수, 가격 5% 하락 시 추가 매수. 장기 적립식 투자.",
        "testimonial": "장기 투자에 가장 안전한 방법",
        "asset_type": "crypto",
        "assets": ["ETHUSDT"],
        "factors": ["price_momentum_short", "ema_alignment"],
        "weights": {"price_momentum_short": 1.0, "ema_alignment": 0.8},
        "risk_level": "low",
        "expected_monthly_return": "2-6%",
    },
    {
        "id": "funding_rate_arb",
        "name": "펀딩비 차익거래",
        "category": "시장중립",
        "description": "선물 펀딩비가 극단적일 때 역방향 진입. 시장 방향과 무관한 수익.",
        "testimonial": "시장이 오르든 내리든 안정적 수익",
        "asset_type": "crypto",
        "assets": ["BTCUSDT"],
        "factors": ["funding_rate_signal", "long_short_ratio", "derivatives_sentiment"],
        "weights": {"funding_rate_signal": 1.5, "long_short_ratio": 1.2, "derivatives_sentiment": 1.0},
        "risk_level": "medium",
        "expected_monthly_return": "5-10%",
    },
    {
        "id": "trend_following_btc",
        "name": "비트코인 추세 추종",
        "category": "공격",
        "description": "200일 이동평균 위에서 매수, 아래로 떨어지면 매도. 클래식 추세 전략.",
        "testimonial": "오래된 기법, 검증된 효과",
        "asset_type": "crypto",
        "assets": ["BTCUSDT"],
        "factors": ["ema_alignment", "trend_consistency", "price_momentum_medium"],
        "weights": {"ema_alignment": 1.5, "trend_consistency": 1.5, "price_momentum_medium": 1.2},
        "risk_level": "medium",
        "expected_monthly_return": "5-15%",
    },
    {
        "id": "mean_reversion_safe",
        "name": "평균 회귀 안전 전략",
        "category": "보수",
        "description": "VWAP에서 멀어진 가격이 다시 돌아올 때까지 기다리는 안전 전략.",
        "testimonial": "큰 손실 없이 꾸준한 수익",
        "asset_type": "crypto",
        "assets": ["BTCUSDT", "ETHUSDT"],
        "factors": ["vwap_reversion", "bb_contrarian", "stochastic_extreme"],
        "weights": {"vwap_reversion": 1.5, "bb_contrarian": 1.3, "stochastic_extreme": 1.0},
        "risk_level": "low",
        "expected_monthly_return": "3-8%",
    },
]


def get_all_templates() -> list[dict]:
    return KOREAN_TEMPLATES


def get_template(template_id: str) -> dict | None:
    for t in KOREAN_TEMPLATES:
        if t["id"] == template_id:
            return t
    return None


def get_by_category(category: str) -> list[dict]:
    return [t for t in KOREAN_TEMPLATES if t["category"] == category]
