#!/usr/bin/env bash
# Assemble the demo video from recorded takes.
#
#   demo/assemble.sh [tour_src] [modal_src]
#
# tour_src  -- take-1 footage (tour + live scrape start); the first TOUR_CUT
#              seconds are kept.
# modal_src -- take-2 footage (ends with the result-modal tour); the last
#              MODAL_TAIL seconds are kept.
# A "time-lapse" slate bridges the cut. Output: demo/job-scraper-demo.mp4
# (1366x768, h264, 30fps, silent).
set -e
cd "$(dirname "$0")"

TOUR_SRC="${1:-takes/page@84609298cb495289522254dfa55acb22.webm}"
MODAL_SRC="${2:-takes/scrape.webm}"
TOUR_CUT="${TOUR_CUT:-185}"
MODAL_TAIL="${MODAL_TAIL:-75}"
OUT="job-scraper-demo.mp4"
FONT="/usr/share/fonts/liberation/LiberationSans-Regular.ttf"
[ -f "$FONT" ] || FONT="/usr/share/fonts/noto/NotoSans-Regular.ttf"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "[assemble] cutting head (${TOUR_CUT}s) from $TOUR_SRC"
ffmpeg -v error -y -i "$TOUR_SRC" -t "$TOUR_CUT" \
  -vf "scale=1366:768,fps=30" -c:v libx264 -preset medium -crf 20 -an \
  "$tmp/head.mp4"

echo "[assemble] cutting tail (${MODAL_TAIL}s) from $MODAL_SRC"
ffmpeg -v error -y -sseof "-$MODAL_TAIL" -i "$MODAL_SRC" \
  -vf "scale=1366:768,fps=30" -c:v libx264 -preset medium -crf 20 -an \
  "$tmp/tail.mp4"

echo "[assemble] rendering time-lapse slate"
ffmpeg -v error -y -f lavfi -i "color=c=0x111111:s=1366x768:r=30:d=3" \
  -vf "drawtext=fontfile=$FONT:text='time-lapse --- scrape running (~50 min condensed)':fontcolor=white:fontsize=34:x=(w-text_w)/2:y=(h-text_h)/2,fps=30" \
  -c:v libx264 -preset medium -crf 20 -an "$tmp/slate.mp4"

printf "file '%s'\nfile '%s'\nfile '%s'\n" \
  "$tmp/head.mp4" "$tmp/slate.mp4" "$tmp/tail.mp4" > "$tmp/list.txt"
ffmpeg -v error -y -f concat -safe 0 -i "$tmp/list.txt" \
  -c:v libx264 -preset medium -crf 23 -movflags +faststart -an "$OUT"

echo "[assemble] wrote $OUT"
ffprobe -v error -show_entries format=duration,size \
  -of default=noprint_wrappers=1 "$OUT"
