#!/bin/bash
# timelapse_timestamp.sh - Create time-lapse with timestamp overlay from EXIF data

FOLDER=${1:-.}
OUTPUT=${2:-output.mp4}
START=${3:-1}
END=${4:-}
FPS=${5:-30}
ROTATE=${6:-cw}
SHOW_TIMESTAMP=${7:-yes}  # yes/no to show timestamp overlay

# Help message
if [ "$1" = "-h" ] || [ "$1" = "--help" ]; then
    echo "Usage: ./timelapse_timestamp.sh [folder] [output] [start] [end] [fps] [rotate] [timestamp]"
    echo ""
    echo "Arguments:"
    echo "  folder     - Input folder with TLS_*.jpg files"
    echo "  output     - Output video filename"
    echo "  start      - First frame number"
    echo "  end        - Last frame number (empty = auto-detect)"
    echo "  fps        - Frame rate (default: 30)"
    echo "  rotate     - Rotation: none, cw, ccw, 180 (default: cw)"
    echo "  timestamp  - Show timestamp overlay: yes/no (default: yes)"
    echo ""
    echo "Examples:"
    echo "  ./timelapse_timestamp.sh ./frames sunset.mp4 1 1800 30 cw yes"
    echo "  ./timelapse_timestamp.sh ./frames output.mp4 1 \"\" 30 none no"
    echo ""
    echo "Requirements:"
    echo "  - exiftool (sudo apt-get install libimage-exiftool-perl)"
    echo ""
    exit 0
fi

# Check for exiftool
if ! command -v exiftool &> /dev/null; then
    echo "Error: exiftool not found"
    echo "Install with: sudo apt-get install libimage-exiftool-perl"
    echo "Or on macOS: brew install exiftool"
    exit 1
fi

# Check if folder exists
if [ ! -d "$FOLDER" ]; then
    echo "Error: Folder '$FOLDER' does not exist"
    exit 1
fi

# Auto-detect end frame if not specified
if [ -z "$END" ]; then
    LAST_FILE=$(ls -1 "$FOLDER"/TLS_*.jpg 2>/dev/null | sort | tail -n1)
    if [ -z "$LAST_FILE" ]; then
        echo "Error: No TLS_*.jpg files found in $FOLDER"
        exit 1
    fi
    END=$(basename "$LAST_FILE" .jpg | sed 's/TLS_0*//')
    [ -z "$END" ] && END=1
    echo "Auto-detected end frame: $END"
fi

# Calculate frame count
FRAMES=$((END - START + 1))
DURATION=$(echo "scale=2; $FRAMES / $FPS" | bc)

# Extract timestamp from first frame to show as example
FIRST_FRAME=$(printf "%s/TLS_%09d.jpg" "$FOLDER" "$START")
if [ -f "$FIRST_FRAME" ]; then
    SAMPLE_TIME=$(exiftool -DateTimeOriginal -d "%Y-%m-%d %H:%M:%S" -s3 "$FIRST_FRAME" 2>/dev/null)
    if [ -n "$SAMPLE_TIME" ]; then
        echo "Sample timestamp from first frame: $SAMPLE_TIME"
    else
        echo "Warning: Could not extract timestamp from $FIRST_FRAME"
        echo "Checking for alternative date fields..."
        SAMPLE_TIME=$(exiftool -CreateDate -d "%Y-%m-%d %H:%M:%S" -s3 "$FIRST_FRAME" 2>/dev/null)
        if [ -n "$SAMPLE_TIME" ]; then
            echo "Found CreateDate: $SAMPLE_TIME"
        else
            echo "No timestamp metadata found. Video will be created without timestamp overlay."
            SHOW_TIMESTAMP="no"
        fi
    fi
fi

# Set rotation filter
case "$ROTATE" in
    cw)   ROT_FILTER="transpose=1" ;;
    ccw)  ROT_FILTER="transpose=2" ;;
    180)  ROT_FILTER="transpose=1,transpose=1" ;;
    none) ROT_FILTER="" ;;
    *)    echo "Unknown rotation: $ROTATE (using none)"; ROT_FILTER="" ;;
esac

# Display settings
echo "=========================================="
echo "Time-lapse Settings:"
echo "=========================================="
echo "Input folder:    $FOLDER"
echo "Output file:     $OUTPUT"
echo "Frame range:     $START to $END ($FRAMES frames)"
echo "Frame rate:      ${FPS} fps"
echo "Duration:        ~${DURATION} seconds"
echo "Rotation:        ${ROTATE}"
echo "Timestamp:       ${SHOW_TIMESTAMP}"
echo "=========================================="
echo ""

# Build filter chain
FILTER_CHAIN=""

# Add rotation if needed
if [ -n "$ROT_FILTER" ]; then
    FILTER_CHAIN="$ROT_FILTER"
fi

# Add timestamp overlay if requested
if [ "$SHOW_TIMESTAMP" = "yes" ]; then
    # drawtext filter to show timestamp from EXIF data
    TIMESTAMP_FILTER="drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:text='%{metadata\:DateTimeOriginal}':fontcolor=white:fontsize=48:box=1:boxcolor=black@0.7:boxborderw=10:x=30:y=30"
    
    if [ -n "$FILTER_CHAIN" ]; then
        FILTER_CHAIN="$FILTER_CHAIN,$TIMESTAMP_FILTER"
    else
        FILTER_CHAIN="$TIMESTAMP_FILTER"
    fi
fi

# Build FFmpeg command
echo "Creating video with timestamp overlay..."
echo ""

if [ -n "$FILTER_CHAIN" ]; then
    ffmpeg -start_number "$START" \
           -framerate "$FPS" \
           -pix_fmt yuvj420p \
           -i "$FOLDER/TLS_%09d.jpg" \
           -frames:v "$FRAMES" \
           -vf "$FILTER_CHAIN" \
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

# Check result
if [ $? -eq 0 ]; then
    echo ""
    echo "=========================================="
    echo "✓ Video created successfully!"
    echo "=========================================="
    ls -lh "$OUTPUT"
else
    echo ""
    echo "✗ Error creating video"
    exit 1
fi
