#!/bin/bash
# make_timelapse.sh - Core time-lapse creation engine
# This script is called by configuration scripts with all parameters
# It handles video encoding, rotation, and timestamp overlay

# ============================================
# PARAMETER ACCEPTANCE
# ============================================
# Accept all parameters from calling script
INPUT_FOLDER="$1"        # Path to folder containing TLS_*.jpg files
OUTPUT_FILE="$2"         # Output video filename
START_FRAME="$3"         # First frame number to process
END_FRAME="$4"           # Last frame number to process
FPS="$5"                 # Frames per second for output video
ROTATE="$6"              # Rotation: none, cw, ccw, 180
SHOW_TIMESTAMP="${7:-yes}"  # Whether to show timestamp overlay (yes/no)

# Timestamp display customization parameters
TIMESTAMP_FONTSIZE="${8:-48}"           # Font size in pixels
TIMESTAMP_FONTCOLOR="${9:-white}"       # Font color (white, yellow, red, etc.)
TIMESTAMP_X="${10:-30}"                 # X position from left edge
TIMESTAMP_Y="${11:-30}"                 # Y position from top edge
TIMESTAMP_BOX="${12:-yes}"              # Show background box (yes/no)
TIMESTAMP_BOXCOLOR="${13:-black@0.7}"   # Box color with transparency (black@0.7 = 70% opaque)
TIMESTAMP_BOXBORDER="${14:-10}"         # Box border/padding in pixels

# ============================================
# INPUT VALIDATION
# ============================================
# Ensure all required parameters are provided
if [ -z "$INPUT_FOLDER" ] || [ -z "$OUTPUT_FILE" ] || [ -z "$START_FRAME" ] || [ -z "$END_FRAME" ]; then
    echo "Error: Missing required parameters"
    echo ""
    echo "Usage: $0 <input_folder> <output_file> <start_frame> <end_frame> <fps> <rotate> [show_timestamp] [fontsize] [fontcolor] [x] [y] [box] [boxcolor] [boxborder]"
    echo ""
    echo "Required parameters:"
    echo "  input_folder  - Path to folder with TLS_*.jpg files"
    echo "  output_file   - Output video filename"
    echo "  start_frame   - First frame number"
    echo "  end_frame     - Last frame number"
    echo "  fps           - Frame rate (default: 30)"
    echo "  rotate        - Rotation: none, cw, ccw, 180 (default: cw)"
    echo ""
    echo "Optional timestamp parameters:"
    echo "  show_timestamp - yes/no (default: yes)"
    echo "  fontsize       - Font size in pixels (default: 48)"
    echo "  fontcolor      - Color name (default: white)"
    echo "  x              - X position from left (default: 30)"
    echo "  y              - Y position from top (default: 30)"
    echo "  box            - Show background box yes/no (default: yes)"
    echo "  boxcolor       - Box color with alpha (default: black@0.7)"
    echo "  boxborder      - Box padding in pixels (default: 10)"
    echo ""
    echo "This script is meant to be called by configuration scripts."
    exit 1
fi

# Set default values for optional parameters
FPS="${FPS:-30}"
ROTATE="${ROTATE:-cw}"

# ============================================
# DISPLAY CONFIGURATION
# ============================================
# Show all settings to user before processing
echo "=========================================="
echo "Time-lapse Video Creator"
echo "=========================================="
echo "Input folder:    $INPUT_FOLDER"
echo "Output file:     $OUTPUT_FILE"
echo "Frame range:     $START_FRAME to $END_FRAME"
echo "Frame rate:      ${FPS} fps"
echo "Rotation:        $ROTATE"
echo "Timestamp:       $SHOW_TIMESTAMP"
if [ "$SHOW_TIMESTAMP" = "yes" ]; then
    echo "  Font size:     ${TIMESTAMP_FONTSIZE}px"
    echo "  Font color:    $TIMESTAMP_FONTCOLOR"
    echo "  Position:      (${TIMESTAMP_X}, ${TIMESTAMP_Y})"
    echo "  Background:    $TIMESTAMP_BOX"
    if [ "$TIMESTAMP_BOX" = "yes" ]; then
        echo "  Box color:     $TIMESTAMP_BOXCOLOR"
        echo "  Box padding:   ${TIMESTAMP_BOXBORDER}px"
    fi
fi
echo "=========================================="
echo ""

# ============================================
# FOLDER VALIDATION
# ============================================
# Check that input folder exists
if [ ! -d "$INPUT_FOLDER" ]; then
    echo "Error: Input folder '$INPUT_FOLDER' does not exist"
    exit 1
fi

# ============================================
# FRAME CALCULATION
# ============================================
# Calculate total number of frames and video duration
FRAMES=$((END_FRAME - START_FRAME + 1))
DURATION=$(echo "scale=2; $FRAMES / $FPS" | bc)
echo "Total frames:    $FRAMES"
echo "Video duration:  ~${DURATION} seconds"
echo ""

# ============================================
# TIMESTAMP SETUP
# ============================================
# Check for exiftool and validate timestamp availability if needed
if [ "$SHOW_TIMESTAMP" = "yes" ]; then
    # Check if exiftool is installed
    if ! command -v exiftool &> /dev/null; then
        echo "Warning: exiftool not found, timestamp overlay disabled"
        echo "Install with: sudo apt-get install libimage-exiftool-perl"
        SHOW_TIMESTAMP="no"
    else
        # Test if first frame has timestamp metadata
        FIRST_FRAME=$(printf "%s/TLS_%09d.jpg" "$INPUT_FOLDER" "$START_FRAME")
        if [ -f "$FIRST_FRAME" ]; then
            # Try DateTimeOriginal first
            SAMPLE_TIME=$(exiftool -DateTimeOriginal -d "%Y-%m-%d %H:%M:%S" -s3 "$FIRST_FRAME" 2>/dev/null)
            # Fall back to CreateDate if DateTimeOriginal not found
            if [ -z "$SAMPLE_TIME" ]; then
                SAMPLE_TIME=$(exiftool -CreateDate -d "%Y-%m-%d %H:%M:%S" -s3 "$FIRST_FRAME" 2>/dev/null)
            fi
            # Display sample or disable if no timestamp found
            if [ -n "$SAMPLE_TIME" ]; then
                echo "Sample timestamp: $SAMPLE_TIME"
            else
                echo "Warning: No timestamp metadata found in images"
                echo "  Checked for: DateTimeOriginal and CreateDate"
                echo "  Timestamp overlay disabled"
                SHOW_TIMESTAMP="no"
            fi
        fi
    fi
fi

# ============================================
# BUILD VIDEO FILTER CHAIN
# ============================================
# Construct FFmpeg filter string based on rotation and timestamp settings
FILTER_CHAIN=""

# --- Rotation Filter ---
# Add rotation transformation based on user selection
case "$ROTATE" in
    cw)
        # 90 degrees clockwise
        FILTER_CHAIN="transpose=1"
        echo "Rotation: 90° clockwise"
        ;;
    ccw)
        # 90 degrees counter-clockwise
        FILTER_CHAIN="transpose=2"
        echo "Rotation: 90° counter-clockwise"
        ;;
    180)
        # 180 degrees (apply 90° twice)
        FILTER_CHAIN="transpose=1,transpose=1"
        echo "Rotation: 180°"
        ;;
    none)
        # No rotation
        FILTER_CHAIN=""
        echo "Rotation: none"
        ;;
    *)
        # Unknown rotation option, default to none
        echo "Warning: Unknown rotation '$ROTATE', using none"
        FILTER_CHAIN=""
        ;;
esac

# --- Timestamp Overlay Filter ---
# Add timestamp text overlay if enabled and available
if [ "$SHOW_TIMESTAMP" = "yes" ]; then
    # Locate a suitable font file for the system
    FONT=""
    if [ -f "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" ]; then
        # Linux/Ubuntu common location
        FONT="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    elif [ -f "/System/Library/Fonts/Helvetica.ttc" ]; then
        # macOS system font
        FONT="/System/Library/Fonts/Helvetica.ttc"
    elif [ -f "/Library/Fonts/Arial Bold.ttf" ]; then
        # macOS alternative
        FONT="/Library/Fonts/Arial Bold.ttf"
    else
        # No font found, FFmpeg will use default
        echo "Warning: Could not find font file, using FFmpeg default"
        FONT=""
    fi
    
    # Build the drawtext filter with all customization options
    # The text uses EXIF metadata from each frame
    if [ -n "$FONT" ]; then
        # With custom font
        TIMESTAMP_FILTER="drawtext=fontfile=$FONT"
    else
        # Without custom font (use FFmpeg default)
        TIMESTAMP_FILTER="drawtext"
    fi
    
    # Add text source (EXIF metadata)
    TIMESTAMP_FILTER="${TIMESTAMP_FILTER}:text='%{metadata\:DateTimeOriginal}'"
    
    # Add font styling
    TIMESTAMP_FILTER="${TIMESTAMP_FILTER}:fontcolor=$TIMESTAMP_FONTCOLOR"
    TIMESTAMP_FILTER="${TIMESTAMP_FILTER}:fontsize=$TIMESTAMP_FONTSIZE"
    
    # Add position
    TIMESTAMP_FILTER="${TIMESTAMP_FILTER}:x=$TIMESTAMP_X"
    TIMESTAMP_FILTER="${TIMESTAMP_FILTER}:y=$TIMESTAMP_Y"
    
    # Add background box if enabled
    if [ "$TIMESTAMP_BOX" = "yes" ]; then
        TIMESTAMP_FILTER="${TIMESTAMP_FILTER}:box=1"
        TIMESTAMP_FILTER="${TIMESTAMP_FILTER}:boxcolor=$TIMESTAMP_BOXCOLOR"
        TIMESTAMP_FILTER="${TIMESTAMP_FILTER}:boxborderw=$TIMESTAMP_BOXBORDER"
    fi
    
    # Combine rotation and timestamp filters
    if [ -n "$FILTER_CHAIN" ]; then
        # Add timestamp after rotation
        FILTER_CHAIN="$FILTER_CHAIN,$TIMESTAMP_FILTER"
    else
        # Only timestamp, no rotation
        FILTER_CHAIN="$TIMESTAMP_FILTER"
    fi
    echo "Timestamp: enabled with custom styling"
fi

echo ""
echo "Starting FFmpeg encoding..."
echo "=========================================="
echo ""

# ============================================
# RUN FFMPEG ENCODING
# ============================================
# Execute FFmpeg with all configured parameters
# -start_number: Which frame to start from
# -framerate: Input frame rate for reading images
# -pix_fmt yuvj420p: Input pixel format (suppresses deprecation warning)
# -i: Input file pattern
# -frames:v: Total number of frames to encode
# -vf: Video filter chain (rotation and/or timestamp)
# -c:v libx264: Use H.264 codec
# -crf 18: Constant rate factor (quality: 18 = visually lossless)
# -preset slow: Encoding preset (slower = better compression)
# -pix_fmt yuv420p: Output pixel format (maximum compatibility)
# -movflags +faststart: Enable web streaming (metadata at start)
# -r: Output frame rate
# -map_metadata 0: Copy metadata from input to output
# -y: Overwrite output file without prompting

if [ -n "$FILTER_CHAIN" ]; then
    # With video filters (rotation and/or timestamp)
    ffmpeg -loglevel quiet -start_number "$START_FRAME" \
           -framerate "$FPS" \
           -pix_fmt yuvj420p \
           -i "$INPUT_FOLDER/TLS_%09d.jpg" \
           -frames:v "$FRAMES" \
           -vf "$FILTER_CHAIN" \
           -c:v libx264 \
           -crf 18 \
           -preset slow \
           -pix_fmt yuv420p \
           -movflags +faststart \
           -r "$FPS" \
           -map_metadata 0 \
           -y "$OUTPUT_FILE"
else
    # Without video filters (direct encoding)
    ffmpeg -loglevel quiet -start_number "$START_FRAME" \
           -framerate "$FPS" \
           -pix_fmt yuvj420p \
           -i "$INPUT_FOLDER/TLS_%09d.jpg" \
           -frames:v "$FRAMES" \
           -c:v libx264 \
           -crf 18 \
           -preset slow \
           -pix_fmt yuv420p \
           -movflags +faststart \
           -r "$FPS" \
           -map_metadata 0 \
           -y "$OUTPUT_FILE"
fi

# Capture FFmpeg exit status
FFMPEG_EXIT=$?

# ============================================
# RESULT CHECKING AND REPORTING
# ============================================
# Verify that video was created successfully
if [ $FFMPEG_EXIT -eq 0 ] && [ -f "$OUTPUT_FILE" ]; then
    # Success - display file information
    echo ""
    echo "=========================================="
    echo "✓ SUCCESS! Video created"
    echo "=========================================="
    ls -lh "$OUTPUT_FILE"
    echo ""
    echo "Output: $OUTPUT_FILE"
    exit 0
else
    # Failure - report error
    echo ""
    echo "=========================================="
    echo "✗ ERROR: Failed to create video"
    echo "=========================================="
    exit 1
fi
