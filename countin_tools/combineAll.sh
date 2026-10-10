#!/usr/bin/bash
set +x
# This script appends three videos to each other, like a video sandwich.
# It uses the middle video's resolution and framerate, for the resulting output.
# We use this for putting a count-in and an animated vanity card at the end of foleyoke clips

TARGET_DIR=$1

function combine() {
    # Check if correct number of arguments provided
    if [ "$#" -ne 4 ]; then
        echo "Usage: $0 <video1> <video2> <video3> <output>"
        echo "Output will be saved as 'output'"
        exit 1
    fi

    VIDEO1="$1"
    VIDEO2="$2"
    VIDEO3="$3"
    OUTPUT="$4"

    # Check if input files exist
    if [ ! -f "$VIDEO1" ]; then
        echo "Error: $VIDEO1 not found"
        exit 1
    fi

    if [ ! -f "$VIDEO2" ]; then
        echo "Error: $VIDEO2 not found"
        exit 1
    fi

    if [ -z "$OUTPUT" ]; then
        echo "no output param. writing to output.mp4"
        OUTPUT="output.mp4"
    fi

    # Get resolution and framerate from second video
    RESOLUTION=$(ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=s=x:p=0 "$VIDEO2")
    FRAMERATE=$(ffprobe -v error -select_streams v:0 -show_entries stream=r_frame_rate -of default=noprint_wrappers=1:nokey=1 "$VIDEO2")

    echo "Target resolution: $RESOLUTION"
    echo "Target framerate: $FRAMERATE"

    # Create a temporary file for the concat list
    CONCAT_FILE=$(mktemp)
    trap "rm -f $CONCAT_FILE" EXIT

    # Concatenate videos using filter_complex to match second video's properties
    ffmpeg -i "$VIDEO1" -i "$VIDEO2" -i "$VIDEO3" \
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
        echo "Error: ffmpeg failed"
        exit 1
    fi
}

for f in $TARGET_DIR/*.mp4; do
    combine 321.mp4 $f FIN_endcard_1920x1080.mp4 out/$(basename $f)
done
