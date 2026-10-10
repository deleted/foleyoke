#!/usr/bin/env bash
set -uo pipefail
# This script appends three videos to each other, like a video sandwich.
# It uses the middle video's resolution and framerate, for the resulting output.
# We use this for putting a count-in and an animated vanity card at the end of foleyoke clips
#
# Usage: combineAll.sh <input_dir> [output_dir]
#   Recurses <input_dir> for *.mp4 and mirrors the subdirectory structure into <output_dir>.

TARGET_DIR="${1:-.}"
OUTPUT_DIR="${2:-out}"

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# Overridable via environment
INTRO="${INTRO:-$SCRIPT_DIR/321.mp4}"
OUTRO="${OUTRO:-$SCRIPT_DIR/FIN_endcard_1920x1080.mp4}"
OVERWRITE="${OVERWRITE:-0}"

function combine() {
    if [ "$#" -ne 4 ]; then
        echo "Usage: combine <video1> <video2> <video3> <output>"
        return 1
    fi

    local VIDEO1="$1"
    local VIDEO2="$2"
    local VIDEO3="$3"
    local OUTPUT="$4"

    for v in "$VIDEO1" "$VIDEO2" "$VIDEO3"; do
        if [ ! -f "$v" ]; then
            echo "Error: $v not found"
            return 1
        fi
    done

    # Get resolution and framerate from second video
    local RESOLUTION FRAMERATE
    RESOLUTION=$(ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=s=x:p=0 "$VIDEO2")
    FRAMERATE=$(ffprobe -v error -select_streams v:0 -show_entries stream=r_frame_rate -of default=noprint_wrappers=1:nokey=1 "$VIDEO2")

    if [ -z "$RESOLUTION" ] || [ -z "$FRAMERATE" ]; then
        echo "Error: could not probe $VIDEO2"
        return 1
    fi

    echo "Target resolution: $RESOLUTION"
    echo "Target framerate: $FRAMERATE"

    # this concatenates all three videos into an output file
    # with the same resolution and framerate detected from the second video

    # concat's "a=0" means zero audio streams, so this produces a video-only output.
    ffmpeg -nostdin -y -i "$VIDEO1" -i "$VIDEO2" -i "$VIDEO3" \
        -filter_complex \
        "[0:v]scale=${RESOLUTION},setsar=1,fps=${FRAMERATE}[v0]; \
   [1:v]scale=${RESOLUTION},setsar=1,fps=${FRAMERATE}[v1]; \
   [2:v]scale=${RESOLUTION},setsar=1,fps=${FRAMERATE}[v2]; \
   [v0][v1][v2]concat=n=3:v=1:a=0[outv]" \
        -map "[outv]" \
        -c:v libx264 -preset medium -crf 23 \
        "$OUTPUT"

    if [ $? -eq 0 ]; then
        echo "Successfully created $OUTPUT"
    else
        echo "Error: ffmpeg failed for $VIDEO2"
        return 1
    fi
}

if [ ! -d "$TARGET_DIR" ]; then
    echo "Error: input directory '$TARGET_DIR' not found"
    exit 1
fi

# Resolve to absolute paths so relative-path math and pruning are reliable
ABS_TARGET=$(cd "$TARGET_DIR" && pwd)
mkdir -p "$OUTPUT_DIR"
ABS_OUT=$(cd "$OUTPUT_DIR" && pwd)

ok=0
failed=0
skipped=0

while IFS= read -r -d '' f; do
    # Never treat our own output as input
    case "$f" in
    "$ABS_OUT"/*) continue ;;
    esac

    rel="${f#"$ABS_TARGET"/}"
    dest="$ABS_OUT/$rel"

    if [ "$OVERWRITE" != "1" ] && [ -f "$dest" ]; then
        echo "Skipping (exists): $rel"
        skipped=$((skipped + 1))
        continue
    fi

    mkdir -p "$(dirname "$dest")"

    echo "=== $rel -> $dest"
    if combine "$INTRO" "$f" "$OUTRO" "$dest"; then
        ok=$((ok + 1))
    else
        failed=$((failed + 1))
    fi
done < <(find "$ABS_TARGET" -type f -iname '*.mp4' -print0 | sort -z)

echo "Done. $ok succeeded, $failed failed, $skipped skipped."
[ "$failed" -eq 0 ]
