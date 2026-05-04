# Upbit 자동매매 운영 가이드

업비트에서 이 시스템으로 자동매매를 돌리기 위한 단계별 가이드입니다.
**처음 1-2주는 반드시 소액(10-50만원)으로 테스트**하고 검증된 후에만 증액하세요.

---

## 📊 예상 성능 (정직한 수치)

8년 백테스트 기준 long-only Sharpe (Upbit 수수료 5bps 반영):
| 알파 | 평균 Sharpe | MDD | 비고 |
|---|---|---|---|
| momentum_ensemble | **1.19** | 5-12% | 가장 안정적 |
| trend_breakout | 0.91 | 27-55% | 고수익·고변동성 |
| kalman_trend | 0.82 | 23-37% | 적응형 |
| vol_breakout | 0.36 | 15-36% | 선택적 진입 |

**현실 디스카운트:**
- 실거래는 slippage + fill-rate로 백테스트 대비 **10-30% 저하** 일반적
- 2026년 현재 시장은 학습 데이터 (2018-2024)와 다를 수 있음
- 따라서 실제 Sharpe **0.8~1.0** 기대하는 게 현실적

---

## 🚀 배포 5단계

### Step 1. Upbit API 키 발급

1. Upbit 웹 → 마이페이지 → **Open API 관리**
2. 키 생성 시 권한:
   - ✅ **자산조회**
   - ✅ **주문내역 조회**
   - ✅ **주문하기**
   - ❌ 출금 (절대 체크하지 말 것)
3. 특정 IP 허용 (서버 IP) — 이 서버에서만 키 동작
4. Access Key, Secret Key 받아서 안전한 곳에 보관

### Step 2. 마스터 암호 설정 + 키 저장

```bash
# 마스터키 생성 (한 번만, 분실 시 저장된 API 키 복구 불가)
export QUANT_MASTER_KEY=$(openssl rand -hex 32)

# 영구화: ~/.bashrc에 추가
echo "export QUANT_MASTER_KEY=$QUANT_MASTER_KEY" >> ~/.bashrc

# Upbit 키 저장 (유효성 검증 포함)
cd /home/ubuntu/quant
python3 -m shared.execution.credentials save upbit <ACCESS_KEY> <SECRET_KEY> --validate
```

성공 시:
```
  Upbit 키 유효성 검증 중...
  ✓ 검증 성공
  ✓ upbit 키 저장 → /home/ubuntu/.quant/credentials.enc
```

### Step 3. 텔레그램 알림 설정 (강력 권장)

```bash
# @BotFather로 봇 생성 → 토큰 받기
# 봇에게 /start 보낸 후 다음 URL에서 chat_id 확인:
# https://api.telegram.org/bot<TOKEN>/getUpdates

export TELEGRAM_BOT_TOKEN=<BOT_TOKEN>
export TELEGRAM_CHAT_ID=<CHAT_ID>

# 영구화
echo "export TELEGRAM_BOT_TOKEN=$TELEGRAM_BOT_TOKEN" >> ~/.bashrc
echo "export TELEGRAM_CHAT_ID=$TELEGRAM_CHAT_ID" >> ~/.bashrc
```

### Step 4. 통합 테스트

```bash
cd /home/ubuntu/quant

# 4-1. Public API + dry-run (키 없이)
python3 scripts/live/test_upbit_integration.py

# 4-2. 6,000원 실거래 (매수→즉시 매도로 end-to-end 검증)
python3 scripts/live/test_upbit_integration.py --live-test

# 4-3. Baseline equity 설정 (P&L 기준점)
python3 scripts/live/live_pnl_report.py --reset-baseline
```

### Step 5. 프로덕션 기동

두 개 데몬을 동시에 실행 (각각 별도 터미널 또는 systemd):

**터미널 1 — 봉-정렬 스케줄러 (봉 닫힘마다 주문)**
```bash
python3 scripts/live/bar_scheduler.py \
    --exchange upbit --live \
    --timeframes 8h
```

**터미널 2 — 실시간 리스크 데몬 (급락 감지)**
```bash
python3 scripts/live/risk_daemon.py \
    --exchange upbit \
    --api-key $(python3 -c "from shared.execution.credentials import load_credentials; print(load_credentials('upbit')[0])") \
    --api-secret $(python3 -c "from shared.execution.credentials import load_credentials; print(load_credentials('upbit')[1])")
```

**cron — 일일 리포트 (00:15 UTC)**
```bash
crontab -e
# 추가:
15 0 * * * cd /home/ubuntu/quant && python3 scripts/live/live_pnl_report.py >> data/logs/pnl.log 2>&1
```

---

## 🛡️ 안전장치 이해

### 자동 정지 조건 (risk_daemon.py)

| 조건 | 임계값 | 동작 |
|---|---|---|
| 5분 내 급락 | -5% | 전량 청산 + HALT |
| 포트폴리오 누적 DD | -10% | 전량 청산 + HALT |
| 일일 손실 | -8% | 전량 청산 + HALT |

HALT 플래그는 `/home/ubuntu/quant/data/state/halt.flag` — 이 파일이 있으면 봉 스케줄러가 새 주문 거부.

**해제 방법:**
```bash
rm /home/ubuntu/quant/data/state/halt.flag
```
(⚠️ 왜 halt됐는지 파일 내용 먼저 확인 후 해제)

### 주문 리스크 한도

`execute_signals.py` 내장:
- 심볼당 최대 25% equity
- 총 gross 최대 100% (long-only)
- 단일 주문 최대 5% equity
- 일일 turnover 최대 200%
- 최소 주문 5,000 KRW (Upbit 규정)

---

## 📈 운영 모니터링

### 로그 위치

```
data/logs/
├── scheduler/        # bar_scheduler tick 로그
├── ledger/           # 모든 주문 JSONL 감사 원장
└── pnl.log           # 일일 리포트
```

### 수동 체크 명령

```bash
# 현재 잔고
python3 -c "
from shared.execution.credentials import load_credentials
from shared.execution.upbit import UpbitConnector
k, s = load_credentials('upbit')
c = UpbitConnector(k, s)
print('Balances:', c.get_balances())
print('Equity:', c.get_account_equity(), 'KRW')
"

# 24시간 거래 요약
python3 -c "
from shared.execution.ledger import fill_summary
print(fill_summary(hours=24))
"

# 시그널 상태 (dry-run으로 오늘의 타깃 확인)
python3 scripts/live/execute_signals.py --exchange upbit --dry-run
```

---

## 🚨 긴급 상황 대응

### 잘못된 주문이 나갔을 때
```bash
# 1. 스케줄러/리스크 데몬 정지
pkill -SIGTERM -f bar_scheduler
pkill -SIGTERM -f risk_daemon

# 2. 모든 포지션 수동 청산 (Upbit 웹 사용 권장)

# 3. halt 플래그 생성 (자동화 차단)
echo '{"halted_at": "manual", "reason": "manual_intervention"}' > data/state/halt.flag
```

### API 키가 노출됐을 때
1. Upbit 웹에서 즉시 키 비활성화
2. 새 키 발급 → `credentials save upbit` 재실행
3. `data/state/halt.flag` 해제하면 재개

### 시스템 재시작 후
- 봉-정렬 스케줄러는 **자동 복구**: 재시작 시 다음 봉 닫힘까지 대기 후 정상 동작
- 리스크 데몬도 자동으로 WebSocket 재연결
- 포지션은 매 tick마다 거래소에서 다시 읽으므로 drift 없음
- 단, `baseline_equity.json`이 있어야 누적 P&L 유지

---

## 📊 1-2주 검증 체크리스트

- [ ] 3일 연속 스케줄러가 오류 없이 동작
- [ ] 텔레그램 알림 정상 수신
- [ ] 체결된 주문과 ledger 기록 일치
- [ ] 일일 P&L 리포트가 Upbit 실제 잔고와 일치
- [ ] 리스크 데몬이 연결 끊김 시 재연결
- [ ] baseline 대비 DD가 10% 이내
- [ ] 거래 수수료가 예상(일 0.1-0.3%)과 유사

모두 ✓되면 증액 고려. **한 번이라도 ❌면 원인 분석 후 재검증.**

---

## ❓ FAQ

**Q. cron 안 써요? cron이 더 안정적일 것 같은데?**
A. cron도 써도 되지만 `bar_scheduler`가 더 정확합니다:
- cron `:05` 실행 → 봉이 +5분 지난 후 같은 데이터로 동작 (지연)
- bar_scheduler `봉 닫힘 +30초` → 갓 닫힌 봉 = 최신 정보
- cron은 crash에서 자동 복구 (OS가 다음 슬롯에 재실행)
- bar_scheduler는 long-running이라 memory leak 가능 (systemd/supervisor 권장)

둘을 병행 시 주문 중복 발생 가능 → 한쪽만 선택.

**Q. Sharpe 1.0이면 실제 수익률 얼마나?**
A. 변동성 타깃 12%라면 연 수익률 ~12% (Sharpe = 수익률/변동성).
100만원 → 1년 후 평균 112만원, 단 40% DD 확률 존재.

**Q. 매수만 하고 매도는 언제?**
A. 매 8시간마다 봉 닫힘에 새 시그널 생성.
시그널이 +에서 0으로 바뀌면 보유분 매도, 음수 시그널이면 숏 대신 flat(현금).

**Q. 여러 코인 동시 보유?**
A. 네. 5개 코인 (BTC/ETH/SOL/XRP/DOGE)에 equity 1/5씩 배분.
각 코인의 시그널 강도에 따라 실제 포지션 크기 결정 (0-20%).
