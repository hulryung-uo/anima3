#!/bin/sh
# One duel arm. $1 name, $2 pair (accounts anima3m{2p-1,2p}), $3 staff account, $4 backend, $5 arena, rest = extra flags
# e.g.  experiments/run_arm.sh jev-sync 2 anima3gm3 jev 3 --sync-a      (logs land in .logs/exp3/<name>)
cd ~/dev/uo/anima3
NAME=$1; PAIR=$2; STAFF=$3; BACKEND=$4; ARENA=$5; shift 5
A=anima3m$(( (PAIR-1)*2 + 1 )); B=anima3m$(( (PAIR-1)*2 + 2 ))
AIM="Open with Energy Bolt; Greater Heal below half; meditate only when the opponent is far."
.venv/bin/python -m anima3.duel --referee server --gm-user "$STAFF" --arena "$ARENA" \
  --a "$A:mage_a:$BACKEND" --b "$B:mage_b:scripted" \
  --rules 5x-mage --armor none --rounds 5 --matches 40 --max-ticks 900 --monitor-base 0 \
  --suffix "$PAIR" --log-dir ".logs/exp3/$NAME" --aim-a "$AIM" --alternate "$@" > ".logs/exp3/$NAME.out" 2>&1
echo "arm $NAME finished $(date '+%H:%M:%S') rc=$?" >> .logs/exp3/arms.done
