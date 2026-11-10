#!/usr/bin/env bash
# Replaces the image's /cron.sh: runs the sync, then reports the exit code to job-heartbeat.
# Live copy: /volume1/config/recyclarr/cron.sh, mounted over /cron.sh.
echo
echo "-------------------------------------------------------------"
echo " Executing Tasks: $(date)"
echo "-------------------------------------------------------------"

ping=http://job-heartbeat:9113/ping/recyclarr
wget -q -O /dev/null -T 10 "$ping/start" || true
log_arg="--log${RECYCLARR_LOG_LEVEL:+ $RECYCLARR_LOG_LEVEL}"
recyclarr sync "$log_arg"
rc=$?
if [ "$rc" -eq 0 ]; then
  wget -q -O /dev/null -T 10 "$ping" || true
else
  wget -q -O /dev/null -T 10 "$ping/fail?msg=exit+$rc" || true
fi
exit "$rc"
