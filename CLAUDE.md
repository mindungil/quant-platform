# CLAUDE.md — Quant Platform 컨텍스트

이 파일은 새 Claude 세션이 이 프로젝트를 빠르게 이해하기 위한 정수. 더 깊은 history는 `~/.claude/projects/-home-ubuntu/*.jsonl` (전임 세션 transcripts).

## 사용자 정체성

- **GitHub identity**: `mindungil` / `alswnsrlf12@naver.com` — 커밋과 remote URL은 이걸 사용.
- **harness userEmail (`jedutools@gmail.com`)는 절대 git에 쓰지 말 것.** 동일 사용자지만 다른 계정.

## 인스턴스 정보 (gil-dev1)

- SSH: `ssh gil-dev1` (alias 등록됨) → 113.198.66.77 port 19098, user ubuntu, `~/.ssh/os_gil.pem`
- 스펙: Ubuntu 24.04, 8 vCPU, 7.7G RAM (+4G swap), 58G disk
- 작업 디렉토리: `~/quant` (compose project = `quant`)
- 외부 포트 매핑 규칙 (오픈스택 NAT, suffix=098):
  - HTTPS 443 → **17098**, HTTP 80 → 18098, SSH 7777 → 19098, 3000 → 13098, 8080 → 10098
- 외부 URL: `https://113.198.66.77:17098/` (frontend) / `/api/*` / `/grafana/`(basicauth admin) / `/prom/`(basicauth)
- Reverse-proxy: Caddy 2.8, self-signed cert (`infra/reverse-proxy/certs/`), config `infra/reverse-proxy/Caddyfile.ip`
- Host Python venv: `~/.venv-quant-host/bin/python3` (cron 및 systemd 스크립트가 이걸 사용)
- Git remote: `origin = github.com/mindungil/quant-platform.git`, PAT는 `~/.git-credentials`

## 목표 & 사용자 선호

- **퀀트 코어가 진짜 돌아가서, 알파가 돈 벌고, 학습이 자동으로 일어나서 사람 손이 점점 덜 가는 시스템 + open-core로 신뢰/생태계.**
- B-track (배포/안정/관측) > A-track (raw performance) — 시스템이 돌아가는 게 우선.
- 큰 단위 페이즈 계획 선호 ("전부 하자, 페이즈 크게"). "ㅇㅇ" = "응 진행해".
- 비판적/객관적 톤 선호. Gold-plating, 불필요한 추상화 금지.
- V5는 **명시적으로 보류**. V1-V4까지가 현재 스택.

## 아키텍처

**6-domain microservice** (compose 17 containers):
- `platform` (api-gateway 8017 + auth-service 8019)
- `market-pipeline` (market-data 8001, feature-store 8002, signal-service 8003, external-data 8020)
- `strategy-lab` (memory 8004, registry 8005, backtest 8007, statistics 8013) + sidecars: learning-loop, gp-discovery, reoptimizer, attribution-daemon, drawdown-monitor
- `intelligence` (crypto-agent 8006, orchestrator 8014)
- `execution` (exchange-adapter 8008, risk 8009, credential-store 8010, order 8011, portfolio 8012)
- `llm-tools` (llm-gateway 8021)
- + `frontend` (Next.js 8018), `db` (Postgres+Timescale+pgvector), `redis`, `nats`, `prometheus`, `grafana`, `reverse-proxy`

**LangGraph 8-phase StateGraph**: gather → detect → recall → select → score → check → execute → record

**모듈 진화**:
- V1: 4-alpha ensemble (momentum/range_rev/vol_breakout/funding_carry), half_kelly
- V2: vol-target overlay, OnlineDSR, FactorDecayMonitor, basis arb
- V3: LearningLoop, Brinson attribution, MakerTakerBandit, Smart Router
- V4: RL Execution (Almgren-Chriss), Capital Tier (PAPER/MICRO/SMALL/MID/FULL — **현재 MICRO**), GP Discovery, Real-Time Reoptimizer

**Methodology**: López de Prado AFML (Triple Barrier, CPCV, DSR, PBO/CSCV), HRP/NCO/Black-Litterman/CVaR, Ledoit-Wolf, Kelly sizing, IC weighting, Marchenko-Pastur denoising, VolTrendRegime + HMM, Walk-forward/OOS

## FormulaMAB (핵심)

- Thompson sampling, Normal-Inverse-Gamma posterior, γ=0.95 decay, ε=15% exploration
- Redis state: `mab:formula:global`, `mab:formula:regimes` (JSON string, NOT hash)
- ArmState fields: `n`, `mean`, `m2`, `total_reward`, `last_updated` (Welford). NOT `mu/alpha/beta/kappa`.
- **GOTCHA**: `_arms` dict가 unknown arm name에 대한 `update()`를 silently drop함 → arm state drift 원인. 항상 화이트리스트 확인.
- Disabled arms: `MAB_DISABLED_ARMS` env로 영구 비활성 (음수 arm 차단).
- Hindsight reward: `max(-1, min(1, price_change_pct / 5.0))` — outcome consumer가 ~1e-5 scale로 PnL 갱신.
- 라이브 vs 백테스트 velocity gap: 4.4% (23× deflation); Lo (2002) SR SE 사용.

## 호스트 자동화 (systemd + cron)

- **systemd**: `sentiment-daemon.service` (sentiment_pipeline.py, venv python, 자동 재시작)
- **crontab (15 active)**:
  - 주간: funding fetch (일 01:00), weekly_refit (일 02:00), impact retrain (일 03:00), funding regime diagnostic (월 00:45)
  - 일별: alt data (06:30), daily_report (00:15), oos_tracker_30d (00:30)
  - 시간별: paper_portfolio (:10), health_check (:15), signal_to_order_bridge --virtual (:20), signal_gen 1h (:05)
  - 6시간: derivatives fetch (:23)
  - 15분: stress_monitor
  - 5분: kimchi_monitor
  - 3x/일: signal_gen 8h (0,8,16)
- **모든 cron 스크립트는 venv python (`/home/ubuntu/.venv-quant-host/bin/python3`)을 사용** — 시스템 python에는 deps 없음.

## 자주 부딪히는 함정 (Schema gotchas)

| 항목 | 올바른 값 | 흔한 실수 |
|---|---|---|
| DB 이름 | `platform`, `market` | ~~`quant`~~ |
| compose 서비스 | `db` | ~~`postgres`~~ |
| `crypto_decisions` JSONB | `payload` | ~~`components`~~ |
| `order_events` JSONB | `fill` (`fill->>'filled_price'`) | |
| 자산 컬럼 | `asset` | ~~`symbol`~~ |
| psycopg URL | strip `postgresql+psycopg://` prefix | |
| Postgres `SET LOCAL` | bind params 불가 → `set_config()` 사용 | |
| Compose `${VAR:?msg}` | v2.37에서 깨짐 → map style 사용 | |

## 개발 워크플로우

```bash
cd ~/quant
git pull
# 코드 수정
git add . && git commit -m "..." && git push
# 서비스 재배포 (변경된 서비스만)
docker compose --profile observability -f docker-compose.yml -f docker-compose.proxy.ip.yml up -d --build <service>
```

**빠른 iteration**: `docker cp` + `__pycache__` 클리어 + restart. 단 stop→reset→start 로 해야 old-process가 Redis에 stale write 안 함.

## Git 보안 이력

- 2026-05-20: 누출된 PAT 4종 (Groq/OpenRouter/OpenAI/GitHub Models) `git filter-repo`로 history 전체 scrub.
- main HEAD: `da03a20` (scrub 후 SHA, 모든 이전 commit 재작성됨).
- backup branch `backup/pre-cutover-20260520` 삭제됨.
- **신규 코드 commit 시 .env / *.env (sentiment-daemon.env 포함) / certs/ 는 절대 stage 안 함.** `.gitignore` 확인 필수.

## 진행 중 / 보류된 작업

- `#78` B: 24h 자연 누적 관찰 (in_progress)
- `#93` E6: Phase E 배포 후 24h 재관찰 (pending)
- `#97` F4: Phase F 배포 후 24h 재관찰 (in_progress)
- **DNS 미적용 상태** — 추후 도메인 붙이면 Caddyfile.ip → 일반 Caddyfile + Let's Encrypt 전환
- **Binance collector 미작동** — Upbit만 candle 수집 중 (네트워크 차단 추정, 컨테이너에서 binance.com 도달성 점검 필요)

## 추가 컨텍스트 위치

- 가장 최근 quant 세션 transcript: `~/.claude/projects/-home-ubuntu/8e797af8-b1bb-4adb-a7e0-d6b9d00f21cf.jsonl` (18MB, May 12-20: V4 모듈, FormulaMAB 디버깅, Phases B/D/E/F, Caddy 마이그레이션)
- 이전 quant 세션: `f98af020-...jsonl` (31MB, Apr 24 - May 4: alpha decay fix, virtual exchange, v4.5)
- 초기 quant 세션: `f51a0efd-...jsonl` (15MB, Apr 16-23: UI 재작업, 보안 audit)
