#!/bin/sh
# Watch arm .out files: errors, arm finishes, and a tally every $STEP matches.
# usage: watch.sh STEP DIR/arm.out ...   (exits when every listed arm has a "finished" line)
STEP=$1; shift; FILES="$*"
tot(){ cat $FILES 2>/dev/null | grep -c "^match "; }
errs(){ cat $FILES 2>/dev/null | grep -cE "never started|Traceback|crashed|MISSING"; }
fin(){ n=0; for f in $FILES; do d=$(dirname $f); a=$(basename $f .out); grep -q "arm $a finished" $d/arms.done 2>/dev/null && n=$((n+1)); done; echo $n; }
last=$(tot); seen_err=$(errs); seen_fin=$(fin); want=$(echo $FILES | wc -w)
while true; do
  n=$(tot); e=$(errs); f=$(fin)
  [ "$e" -gt "$seen_err" ] && { cat $FILES | grep -E "never started|Traceback|crashed|MISSING" | tail -n $((e-seen_err)); seen_err=$e; }
  [ "$f" -gt "$seen_fin" ] && { echo "arms finished: $f/$want"; seen_fin=$f; }
  if [ $((n/STEP)) -gt $((last/STEP)) ]; then
    line="[$n matches]"
    for x in $FILES; do
      s=$(grep "^match " $x | sed -E 's/.*: [A-Za-z0-9]+ ([0-9]+) - ([0-9]+) .*/\1 \2/' | awk '{w+=$1;l+=$2} END{printf "%d-%d", w, l}')
      line="$line $(basename $x .out) $s($(grep -c '^match ' $x)m)"
    done; echo "$line"; last=$n
  fi
  [ "$f" -ge "$want" ] && exit 0; sleep 60
done
