#!/bin/bash
# batch_timelapse.sh - Create multiple time-lapse videos from frames as
# specified by a config file

CONFIG_FILE=${1:-timelapse_config.txt}

# Help message
if [ "$1" = "-h" ] || [ "$1" = "--help" ]; then
    echo "Usage: ./batch_timelapse.sh [config_file]"
    echo ""
    echo "Config file format (one video per line):"
    echo "  date,folder,start_frame,end_frame,fps,rotate"
    echo ""
    echo "Example config file (timelapse_config.txt):"
    echo "  2024-01-15,./sunset_frames,1,1800,30,cw"
    echo "  2024-01-16,./clouds_001,100,2500,30,none"
    echo "  2024-01-17,./city_timelapse,1,,30,cw"
    echo ""
    echo "Notes:"
    echo "  - Lines starting with # are ignored (comments)"
    echo "  - Empty end_frame will auto-detect"
    echo "  - Output files named: timelapse_YYYY-MM-DD.mp4"
    echo ""
    exit 0
fi

# Check if config file exists
if [ ! -f "$CONFIG_FILE" ]; then
    echo "Error: Config file '$CONFIG_FILE' not found"
    echo ""
    echo "Create a config file with this format:"
    echo "  date,folder,start_frame,end_frame,fps,rotate"
    echo ""
    echo "Example:"
    echo "  2024-01-15,./sunset_frames,1,1800,30,cw"
    echo "  2024-01-16,./clouds_001,100,2500,30,none"
    echo ""
    exit 1
fi

# Process each line in config file
echo "=========================================="
echo "Batch Time-lapse Creator"
echo "=========================================="
echo "Config file: $CONFIG_FILE"
echo ""

LINE_NUM=0
SUCCESS_COUNT=0
FAIL_COUNT=0

while IFS=',' read -r DATE FOLDER START END FPS ROTATE || [ -n "$DATE" ]; do
    LINE_NUM=$((LINE_NUM + 1))
    
    # Skip empty lines and comments
    [[ -z "$DATE" || "$DATE" =~ ^[[:space:]]*# ]] && continue
    
    # Trim whitespace
    DATE=$(echo "$DATE" | xargs)
    FOLDER=$(echo "$FOLDER" | xargs)
    START=$(echo "$START" | xargs)
    END=$(echo "$END" | xargs)
    FPS=$(echo "$FPS" | xargs)
    ROTATE=$(echo "$ROTATE" | xargs)
    
    # Set defaults
    FPS=${FPS:-30}
    ROTATE=${ROTATE:-cw}
    OUTPUT="timelapse_${DATE}.mp4"
    
    echo "=========================================="
    echo "Processing line $LINE_NUM: $DATE"
    echo "=========================================="
    echo "Folder:      $FOLDER"
    echo "Start:       $START"
    echo "End:         ${END:-auto-detect}"
    echo "FPS:         $FPS"
    echo "Rotation:    $ROTATE"
    echo "Output:      $OUTPUT"
    echo ""
    
    # Validate folder exists
    if [ ! -d "$FOLDER" ]; then
        echo "✗ Error: Folder '$FOLDER' does not exist"
        echo ""
        FAIL_COUNT=$((FAIL_COUNT + 1))
        continue
    fi
    
    # Auto-detect end frame if empty
    if [ -z "$END" ]; then
        LAST_FILE=$(ls -1 "$FOLDER"/TLS_*.jpg 2>/dev/null | sort | tail -n1)
        if [ -z "$LAST_FILE" ]; then
            echo "✗ Error: No TLS_*.jpg files found in $FOLDER"
            echo ""
            FAIL_COUNT=$((FAIL_COUNT + 1))
            continue
        fi
        END=$(basename "$LAST_FILE" .jpg | sed 's/TLS_0*//')
        [ -z "$END" ] && END=1
        echo "Auto-detected end frame: $END"
    fi
    
    # Calculate frames
    FRAMES=$((END - START + 1))
    DURATION=$(echo "scale=2; $FRAMES / $FPS" | bc)
    echo "Frames:      $FRAMES"
    echo "Duration:    ~${DURATION}s"
    echo ""
    
    # Set rotation filter
    case "$ROTATE" in
        cw)   VFILTER="transpose=1" ;;
        ccw)  VFILTER="transpose=2" ;;
        180)  VFILTER="transpose=1,transpose=1" ;;
        none) VFILTER="" ;;
        *)    echo "Warning: Unknown rotation '$ROTATE', using none"; VFILTER="" ;;
    esac
    
   # Build and run FFmpeg command
    if [ -n "$VFILTER" ]; then
        ffmpeg -start_number "$START" \
               -framerate "$FPS" \
               -pix_fmt yuvj420p \
               -i "$FOLDER/TLS_%09d.jpg" \
               -frames:v "$FRAMES" \
               -vf "$VFILTER" \
               -c:v libx264 \
               -crf 18 \
               -preset slow \
               -pix_fmt yuv420p \
               -movflags +faststart \
               -r "$FPS" \
               -y "$OUTPUT"
    else
        ffmpeg -start_number "$START" \
               -framerate "$FPS" \
               -pix_fmt yuvj420p \
               -i "$FOLDER/TLS_%09d.jpg" \
               -frames:v "$FRAMES" \
               -c:v libx264 \
               -crf 18 \
               -preset slow \
               -pix_fmt yuv420p \
               -movflags +faststart \
               -r "$FPS" \
               -y "$OUTPUT"
    fi
    
    FFMPEG_EXIT=$?
    
    # Check result
    if [ $FFMPEG_EXIT -eq 0 ] && [ -f "$OUTPUT" ]; then
        echo ""
        echo "✓ Success: $OUTPUT created"
        ls -lh "$OUTPUT"
        SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
    else
        echo ""
        echo "✗ Failed to create $OUTPUT"
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
    echo ""
done < "$CONFIG_FILE"

# Summary
echo "=========================================="
echo "Batch Processing Complete"
echo "=========================================="
echo "Total videos processed: $((SUCCESS_COUNT + FAIL_COUNT))"
echo "Successful: $SUCCESS_COUNT"
echo "Failed: $FAIL_COUNT"
echo "=========================================="
