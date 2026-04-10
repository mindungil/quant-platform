#!/bin/bash
# Setup cron jobs for v4 live signal generation.
# Run: bash scripts/live/cron_setup.sh

REPO="/home/ubuntu/quant"
PYTHON="$(which python3)"
LOG_DIR="$REPO/data/logs"
mkdir -p "$LOG_DIR"

# Write crontab
(
  # Hourly: generate signals for all 5 symbols
  echo "5 * * * * cd $REPO && $PYTHON scripts/live/generate_signals.py >> $LOG_DIR/signals.log 2>&1"
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
