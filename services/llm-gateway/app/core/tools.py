"""Agent Tool Definitions — LLM이 호출할 수 있는 도구 스키마.

각 도구는 Anthropic tool_use / OpenAI function_calling 호환 JSON Schema로 정의.
도구 유형:
  - Atomic: 단일 서비스 호출 (get_market_data, place_order 등)
  - Runbook: 행동 규칙/판단 기준 조회 (get_trading_rules 등)
  - Composite: 여러 서비스를 순차 호출하는 파이프라인
"""
from __future__ import annotations

TOOL_DEFINITIONS: list[dict] = [
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Atomic: 데이터 조회
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    {
        "name": "get_market_data",
        "description": "특정 자산의 최근 OHLCV 캔들 데이터를 조회합니다. 시세, 가격 추이, 거래량 확인에 사용합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "asset": {
                    "type": "string",
                    "description": "자산 심볼 (예: BTCUSDT, ETHUSDT, SOLUSDT)",
                },
                "limit": {
                    "type": "integer",
                    "description": "조회할 캔들 수 (기본 50, 최대 500)",
                    "default": 50,
                },
            },
            "required": ["asset"],
        },
    },
    {
        "name": "get_features",
        "description": "자산의 최신 기술 지표를 조회합니다. RSI, MACD, 볼린저밴드, EMA, SMA, Stochastic, VWAP, ATR, ADX, OBV 등 20개 이상의 지표를 반환합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "asset": {
                    "type": "string",
                    "description": "자산 심볼 (예: BTCUSDT)",
                },
            },
            "required": ["asset"],
        },
    },
    {
        "name": "get_signal",
        "description": "자산의 최신 매매 시그널을 조회합니다. signal_score, 방향(bullish/bearish), 임계값 돌파 여부, 구성 지표별 점수를 반환합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "asset": {
                    "type": "string",
                    "description": "자산 심볼",
                },
            },
            "required": ["asset"],
        },
    },
    {
        "name": "get_portfolio",
        "description": "현재 포트폴리오 상태를 조회합니다. 보유 포지션, 평균 진입가, 미실현/실현 PnL, 집중도, 총 익스포져를 반환합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": "유저 ID (기본: 현재 유저)",
                },
            },
            "required": [],
        },
    },
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Atomic: 분석
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    {
        "name": "detect_regime",
        "description": "현재 시장 레짐을 분석합니다. 추세 강도(trending/sideways), 변동성(low/normal/high), 모멘텀(bullish/bearish/neutral)을 판단하고 적합한 공식 유형을 추천합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "asset": {
                    "type": "string",
                    "description": "자산 심볼",
                },
            },
            "required": ["asset"],
        },
    },
    {
        "name": "search_memory",
        "description": "메모리 서비스에서 유사한 과거 의사결정 기록을 검색합니다. 과거 유사 상황에서의 판단, 사용된 공식, 결과를 참조할 수 있습니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "asset": {
                    "type": "string",
                    "description": "자산 심볼",
                },
                "signal_score": {
                    "type": "number",
                    "description": "현재 시그널 점수 (-1 ~ 1)",
                },
                "action": {
                    "type": "string",
                    "enum": ["BUY", "SELL", "HOLD"],
                    "description": "매매 방향 필터",
                },
                "top_k": {
                    "type": "integer",
                    "description": "반환할 결과 수 (기본 5)",
                    "default": 5,
                },
            },
            "required": ["asset"],
        },
    },
    {
        "name": "search_formula_outcomes",
        "description": "특정 레짐에서 각 공식의 과거 성과를 검색합니다. 어떤 공식이 현재 시장 상황에서 가장 효과적이었는지 판단할 수 있습니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "regime_label": {
                    "type": "string",
                    "description": "시장 레짐 라벨 (예: trending_high_bullish)",
                },
                "asset": {
                    "type": "string",
                    "description": "자산 심볼 필터 (선택)",
                },
                "formula_name": {
                    "type": "string",
                    "description": "특정 공식만 필터 (선택)",
                },
                "top_k": {
                    "type": "integer",
                    "description": "반환할 결과 수",
                    "default": 10,
                },
            },
            "required": ["regime_label"],
        },
    },
    {
        "name": "get_risk_assessment",
        "description": "주문 전 리스크 평가를 수행합니다. VaR(95%), CVaR(95%), 변동성 레짐, 최대 허용 포지션 크기를 반환합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "asset": {
                    "type": "string",
                    "description": "자산 심볼",
                },
                "requested_notional": {
                    "type": "number",
                    "description": "요청 주문 금액 (USD)",
                },
                "current_drawdown": {
                    "type": "number",
                    "description": "현재 드로다운 비율 (0~1)",
                    "default": 0.0,
                },
            },
            "required": ["asset", "requested_notional"],
        },
    },
    {
        "name": "evaluate_formula",
        "description": "공식의 과거 성과를 백테스트합니다. Sharpe ratio, Sortino ratio, 최대 낙폭, 승률, 총 수익률 등 상세 지표를 반환합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "strategy_id": {
                    "type": "string",
                    "description": "전략 ID",
                },
                "weights": {
                    "type": "object",
                    "description": "지표 가중치 (예: {\"rsi_14\": 0.3, \"macd\": 0.4})",
                },
                "asset": {
                    "type": "string",
                    "description": "자산 심볼",
                    "default": "BTCUSDT",
                },
                "sample_size": {
                    "type": "integer",
                    "description": "백테스트 샘플 수",
                    "default": 500,
                },
            },
            "required": ["strategy_id", "weights"],
        },
    },
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Atomic: 행동
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    {
        "name": "place_order",
        "description": "매매 주문을 실행합니다. 리스크 서비스 승인 → 거래소 주문 → 포트폴리오 업데이트 파이프라인을 거칩니다. 반드시 get_risk_assessment로 리스크를 먼저 확인하세요.",
        "input_schema": {
            "type": "object",
            "properties": {
                "asset": {
                    "type": "string",
                    "description": "자산 심볼 (예: BTCUSDT)",
                },
                "side": {
                    "type": "string",
                    "enum": ["BUY", "SELL"],
                    "description": "매매 방향",
                },
                "quantity": {
                    "type": "number",
                    "description": "수량",
                },
                "requested_notional": {
                    "type": "number",
                    "description": "주문 금액 (USD)",
                },
                "stop_loss_pct": {
                    "type": "number",
                    "description": "손절 비율 (예: 0.03 = 3%)",
                },
                "take_profit_pct": {
                    "type": "number",
                    "description": "익절 비율 (예: 0.05 = 5%)",
                },
            },
            "required": ["asset", "side", "quantity", "requested_notional"],
        },
    },
    {
        "name": "store_memory",
        "description": "분석 결과나 의사결정을 메모리에 기록합니다. 나중에 유사 상황에서 참조할 수 있습니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "asset": {
                    "type": "string",
                    "description": "자산 심볼",
                },
                "signal_score": {
                    "type": "number",
                    "description": "시그널 점수",
                },
                "action": {
                    "type": "string",
                    "enum": ["BUY", "SELL", "HOLD"],
                    "description": "판단한 행동",
                },
                "reasoning": {
                    "type": "string",
                    "description": "판단 근거 설명",
                },
                "formula_name": {
                    "type": "string",
                    "description": "사용한 공식 이름 (선택)",
                },
                "regime_label": {
                    "type": "string",
                    "description": "시장 레짐 라벨 (선택)",
                },
            },
            "required": ["asset", "signal_score", "action", "reasoning"],
        },
    },
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Atomic: 공식/전략 관리
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    {
        "name": "list_formulas",
        "description": "사용 가능한 수학 공식 목록을 조회합니다. 레짐별 필터링이 가능합니다. 각 공식의 이름, 설명, 적합한 시장 조건을 반환합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "regime": {
                    "type": "string",
                    "description": "시장 레짐으로 필터 (trending/sideways/reversal/breakout/any)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "list_strategies",
        "description": "등록된 전략 목록을 조회합니다. 상태별(DRAFT/ACTIVE/PAUSED) 필터링이 가능합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "asset_type": {
                    "type": "string",
                    "description": "자산 유형 필터 (crypto)",
                },
                "status": {
                    "type": "string",
                    "description": "전략 상태 필터 (DRAFT/ACTIVE/PAUSED/ARCHIVED)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "register_formula",
        "description": "새로운 공식/모델 버전을 등록합니다. DRAFT 상태로 생성되며, 백테스트 후 promote로 활성화할 수 있습니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "공식/모델 이름",
                },
                "asset_type": {
                    "type": "string",
                    "description": "자산 유형",
                    "default": "crypto",
                },
                "config": {
                    "type": "object",
                    "description": "공식 설정 (지표, 가중치, 파라미터)",
                },
            },
            "required": ["name", "config"],
        },
    },
    {
        "name": "promote_formula",
        "description": "DRAFT 상태의 공식을 ACTIVE로 승격합니다. 이전 ACTIVE 버전은 자동으로 DEPRECATED됩니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "model_id": {
                    "type": "string",
                    "description": "승격할 모델 ID",
                },
            },
            "required": ["model_id"],
        },
    },
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Runbook: 행동 강령 / 판단 기준
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    {
        "name": "get_trading_rules",
        "description": "매매 전 반드시 확인해야 하는 규칙을 조회합니다. 포지션 한도, 리스크 제약, 거래 시간, 최소 주문 금액 등 현재 적용 중인 모든 규칙을 반환합니다.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_formula_guide",
        "description": "레짐별 공식 선택 가이드를 조회합니다. 각 시장 상황(추세/횡보/변동성)에서 어떤 공식이 적합한지, 과거 성과 데이터 기반의 추천을 반환합니다.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Composite: 파이프라인
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    {
        "name": "full_market_analysis",
        "description": "종합 시장 분석을 수행합니다. 시세 → 기술지표 → 레짐 판단 → 메모리 검색 → 공식 추천까지 전체 파이프라인을 한 번에 실행합니다. 빠른 분석이 필요할 때 사용합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "asset": {
                    "type": "string",
                    "description": "자산 심볼 (예: BTCUSDT)",
                },
            },
            "required": ["asset"],
        },
    },
]

# Tool name → definition lookup
TOOL_MAP: dict[str, dict] = {t["name"]: t for t in TOOL_DEFINITIONS}

# Anthropic API format
def get_anthropic_tools() -> list[dict]:
    """Anthropic Messages API tool format."""
    return [
        {
            "name": t["name"],
            "description": t["description"],
            "input_schema": t["input_schema"],
        }
        for t in TOOL_DEFINITIONS
    ]

# OpenAI API format
def get_openai_tools() -> list[dict]:
    """OpenAI function calling format."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in TOOL_DEFINITIONS
    ]
