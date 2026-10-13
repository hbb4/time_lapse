#!/bin/bash
# Pack every TLST* frame folder under ROOT into a verified lossless-JPEG-XL tar
# in ROOT/_packed and delete the original frames, smallest folder first.
#
# Per folder:
#   1. pack + verify with pack_frames.py, straight onto the drive when it has
#      room, otherwise onto the internal disk (LOCAL_DIR);
#   2. originals are deleted by pack_frames.py only after every frame restored
#      from the tar matches its sha256;
#   3. a tar packed locally is then copied to ROOT/_packed, its sha256 compared
#      with the local copy, and the local copy removed.
#
# Safe to interrupt and re-run: finished folders no longer exist and are
# skipped, and a folder caught mid-way is picked up from its existing tar.
# Stops on any verification failure, checksum mismatch, or lack of space.
# Empty folders and the camera's .mp4 files are left alone.
#
# Runs politely: half the cores by default (--workers N to change) and at
# lowered CPU priority (NICE, default 10; NICE=0 for normal priority).
#
# Usage:
#   ./reclaim_space.sh --dry-run        list what would be done
#   ./reclaim_space.sh --limit 1        process only the next folder
#   ./reclaim_space.sh --workers 3      use 3 parallel cjxl/djxl jobs
#   caffeinate -i ./reclaim_space.sh    process everything

set -u -o pipefail

ROOT="${ROOT:-/Volumes/CORSAIR/ATLI_videos_and_stills}"
LOCAL_DIR="${LOCAL_DIR:-$HOME/time_lapse_packed}"
LOCAL_MIN_FREE_MB="${LOCAL_MIN_FREE_MB:-5120}"   # keep this much free on the internal disk
DRIVE_MIN_FREE_MB="${DRIVE_MIN_FREE_MB:-512}"    # keep this much free on the frame drive
NICE="${NICE:-10}"                               # niceness for this script and everything it runs

HERE="$(cd "$(dirname "$0")" && pwd)"
PACK="$HERE/pack_frames.py"
LOG="${LOG:-$HERE/reclaim_space.log}"
PACKED="$ROOT/_packed"

DRY_RUN=0
LIMIT=0
WORKERS=$(( $(sysctl -n hw.ncpu 2>/dev/null || echo 4) / 2 ))   # half the cores
[ "$WORKERS" -ge 1 ] || WORKERS=1
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        --limit) LIMIT="$2"; shift ;;
        --workers) WORKERS="${2:-}"; shift ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

case "$WORKERS" in ''|*[!0-9]*|0) echo "--workers needs a positive integer" >&2; exit 2 ;; esac

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" | tee -a "$LOG"; }
die() { log "STOP: $*"; exit 1; }
free_mb() { df -m "$1" | awk 'NR==2 {print $4}'; }
size_mb() { echo $(( ($(stat -f %z "$1") + 1048575) / 1048576 )); }
sha() { shasum -a 256 "$1" | awk '{print $1}'; }
count_frames() { ls -f "$1" 2>/dev/null | grep -c '^TLS_.*\.jpg$'; }
run_pack() { python3 "$PACK" "$@" --workers "$WORKERS" 2>&1 | tee -a "$LOG"; return "${PIPESTATUS[0]}"; }

# Copy a locally packed tar onto the drive, confirm the checksum, drop the local copy.
move_to_drive() {
    local name="$1" local_tar="$LOCAL_DIR/$1.tar" final="$PACKED/$1.tar"
    if [ ! -f "$final" ]; then
        local need=$(( $(size_mb "$local_tar") + DRIVE_MIN_FREE_MB ))
        [ "$(free_mb "$ROOT")" -ge "$need" ] || die "$name: drive has $(free_mb "$ROOT") MB free, need $need MB to receive the tar; local copy kept at $local_tar"
        log "$name: copying tar to $final"
        rm -f "$final.partial"
        cp "$local_tar" "$final.partial" || die "$name: copy failed; local copy kept at $local_tar"
        [ "$(sha "$local_tar")" = "$(sha "$final.partial")" ] || die "$name: checksum mismatch after copy; local copy kept at $local_tar"
        mv "$final.partial" "$final"
    else
        [ "$(sha "$local_tar")" = "$(sha "$final")" ] || die "$name: $local_tar and $final differ"
    fi
    log "$name: tar on drive matches local copy; removing local copy"
    rm -f "$local_tar"
}

process() {
    local name="$1" src="$ROOT/$1" local_tar="$LOCAL_DIR/$1.tar" final="$PACKED/$1.tar" rc
    log "$name: start ($(count_frames "$src") frames; free: drive $(free_mb "$ROOT") MB, internal $(free_mb "$LOCAL_DIR") MB)"

    if [ -d "$src" ]; then
        if [ -f "$final" ]; then
            log "$name: resuming from $final"
            run_pack "$src" --existing-tar "$final" --tmp-dir "$LOCAL_DIR/tmp" --delete-originals || die "$name: verification of $final failed"
        elif [ -f "$local_tar" ]; then
            log "$name: resuming from $local_tar"
            run_pack "$src" --existing-tar "$local_tar" --tmp-dir "$LOCAL_DIR/tmp" --delete-originals || die "$name: verification of $local_tar failed"
        else
            rm -f "$final.partial" "$local_tar.partial"
            run_pack "$src" --out-dir "$PACKED" --tmp-dir "$LOCAL_DIR/tmp" --min-free-mb "$DRIVE_MIN_FREE_MB" --delete-originals
            rc=$?
            if [ "$rc" -eq 3 ]; then
                log "$name: no room on the drive yet, packing to $LOCAL_DIR"
                run_pack "$src" --out-dir "$LOCAL_DIR" --tmp-dir "$LOCAL_DIR/tmp" --min-free-mb "$LOCAL_MIN_FREE_MB" --delete-originals
                rc=$?
            fi
            [ "$rc" -eq 0 ] || die "$name: pack_frames.py exited $rc"
        fi
    fi

    [ -f "$local_tar" ] && move_to_drive "$name"
    [ -f "$final" ] || die "$name: no tar at $final after processing"
    log "$name: done ($(size_mb "$final") MB tar; drive now has $(free_mb "$ROOT") MB free)"
}

[ -d "$ROOT" ] || { echo "$ROOT not found (is the drive mounted?)" >&2; exit 1; }
command -v cjxl >/dev/null && command -v djxl >/dev/null || { echo "cjxl/djxl not found (brew install jpeg-xl)" >&2; exit 1; }

# Work list: tars still on the internal disk first (work in flight), then
# frame folders by frame count, smallest first.
todo=()
for t in "$LOCAL_DIR"/TLST*.tar; do
    [ -f "$t" ] && todo+=("$(basename "$t" .tar)")
done
while read -r n name; do
    [ -f "$LOCAL_DIR/$name.tar" ] && continue
    if [ "$n" -eq 0 ] && [ ! -f "$PACKED/$name.tar" ]; then
        continue   # empty folder, nothing to pack
    fi
    todo+=("$name")
done < <(for d in "$ROOT"/TLST*/; do
            [ -d "$d" ] || continue
            name="$(basename "$d")"
            echo "$(count_frames "$d") $name"
         done | sort -n)

if [ "$DRY_RUN" -eq 1 ]; then
    echo "${#todo[@]} folders to process (drive free $(free_mb "$ROOT") MB, internal free $(df -m "$HOME" | awk 'NR==2 {print $4}') MB):"
    for name in ${todo[@]+"${todo[@]}"}; do
        echo "  $name  $(count_frames "$ROOT/$name") frames"
    done
    exit 0
fi

mkdir -p "$LOCAL_DIR/tmp" "$PACKED"
renice -n "$NICE" -p $$ >/dev/null 2>&1 || true   # inherited by pack_frames.py, cjxl, djxl, cp, shasum
log "=== reclaim_space start: ${#todo[@]} folders to process, $WORKERS workers, nice $NICE ==="
done_count=0
for name in ${todo[@]+"${todo[@]}"}; do
    process "$name"
    done_count=$((done_count + 1))
    [ "$LIMIT" -gt 0 ] && [ "$done_count" -ge "$LIMIT" ] && break
done
log "=== reclaim_space finished: $done_count folders processed this run ==="
