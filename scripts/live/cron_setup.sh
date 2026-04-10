#!/bin/bash
# Setup cron jobs for v4 live signal generation.
# Run: bash scripts/live/cron_setup.sh

REPO="/home/ubuntu/quant"
PYTHON="$(which python3)"
LOG_DIR="$REPO/data/logs"
mkdir -p "$LOG_DIR"

# Write crontab
(
  # Hourly: generate 1h signals for all 5 symbols
  echo "5 * * * * cd $REPO && $PYTHON scripts/live/generate_signals.py --timeframe 1h >> $LOG_DIR/signals.log 2>&1"
  # Every 8h (00:05, 08:05, 16:05): generate 8h signals (deployment config)
  echo "5 0,8,16 * * * cd $REPO && $PYTHON scripts/live/generate_signals.py --timeframe 8h >> $LOG_DIR/signals_8h.log 2>&1"
  # Hourly +10min: update paper portfolio with latest signals
  echo "10 * * * * cd $REPO && $PYTHON scripts/live/paper_portfolio.py update >> $LOG_DIR/paper.log 2>&1"
  # Daily at 00:15 UTC: performance report
  echo "15 0 * * * cd $REPO && $PYTHON scripts/live/daily_report.py >> $LOG_DIR/daily.log 2>&1"
  # Weekly Sunday 01:00: fetch latest funding rates
  echo "0 1 * * 0 cd $REPO && $PYTHON scripts/data/fetch_funding_rate.py >> $LOG_DIR/funding.log 2>&1"
) | crontab -

echo "Cron jobs installed:"
crontab -l
echo ""
echo "Logs will be at: $LOG_DIR/"
echo "Signal JSONs at: $REPO/data/signals/"
