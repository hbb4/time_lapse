import os
import sys
import subprocess
import math
from datetime import datetime, timedelta

# San Francisco Coordinates
LATITUDE = 37.791667734079596
LONGITUDE = -122.41549323195979

def get_local_offset(dt):
    """Returns UTC offset for SF (PST=-8, PDT=-7)."""
    # Quick DST check for SF (simplified)
    # 2025: March 9 - Nov 2
    # 2026: March 8 - Nov 1
    year = dt.year
    if year == 2025:
        dst_start, dst_end = datetime(2025, 3, 9, 2), datetime(2025, 11, 2, 2)
    elif year == 2026:
        dst_start, dst_end = datetime(2026, 3, 8, 2), datetime(2026, 11, 1, 2)
    else:
        return -8 # Default PST
        
    if dst_start <= dt < dst_end:
        return -7
    return -8

def get_sun_time(date, event="sunrise"):
    """Calculates sunrise/sunset for SF using Solar Position algorithm."""
    day_of_year = date.timetuple().tm_yday
    gamma = (2 * math.pi / 365.0) * (day_of_year - 1)
    
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(gamma) - 0.032077 * math.sin(gamma) \
             - 0.014615 * math.cos(2 * gamma) - 0.040849 * math.sin(2 * gamma))
    
    decl = 0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma) \
           - 0.006758 * math.cos(2 * gamma) + 0.000907 * math.sin(2 * gamma) \
           - 0.002697 * math.cos(3 * gamma) + 0.00148 * math.sin(3 * gamma)
    
    zenith = math.radians(90.833)
    rad_lat = math.radians(LATITUDE)
    
    cos_ha = (math.cos(zenith) / (math.cos(rad_lat) * math.cos(decl))) - (math.tan(rad_lat) * math.tan(decl))
    if cos_ha > 1 or cos_ha < -1: return None
        
    ha = math.acos(cos_ha)
    ha_deg = math.degrees(ha)
    
    solar_noon_utc = 720 - (4 * LONGITUDE) - eqtime
    t1, t2 = solar_noon_utc - (4 * ha_deg), solar_noon_utc + (4 * ha_deg)
    
    morning_utc, evening_utc = min(t1, t2), max(t1, t2)
    utctime = morning_utc if event == "sunrise" else evening_utc
    
    offset = get_local_offset(date)
    local_time_min = (utctime + (offset * 60)) % 1440
    return datetime(date.year, date.month, date.day, int(local_time_min // 60), int(local_time_min % 60))

def get_exif_timestamp(filepath):
    cmd = ["exiftool", "-DateTimeOriginal", "-CreateDate", "-d", "%Y-%m-%d %H:%M:%S", "-s3", filepath]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0 and result.stdout.strip():
        return datetime.strptime(result.stdout.strip().split('\n')[0], "%Y-%m-%d %H:%M:%S")
    return None

class GlobalTimeline:
    def __init__(self, root_dir):
        self.frames = [] # list of (timestamp, absolute_path)
        print("Indexing drive... this may take a minute.")
        CUTOFF_DATE = datetime(2025, 3, 20)
        for root, dirs, files in os.walk(root_dir):
            if "thumbnail" in root: continue
            tls_files = sorted([f for f in files if f.startswith("TLS_") and f.endswith(".jpg")])
            if not tls_files: continue
            
            # Sample first to check date
            t0 = get_exif_timestamp(os.path.join(root, tls_files[0]))
            if not t0 or t0 < CUTOFF_DATE: continue
            
            tn = get_exif_timestamp(os.path.join(root, tls_files[-1]))
            if not tn: continue
            
            # Assuming 10s intervals for all files in this folder
            # For robustness, we'd check more, but let's stick to the pattern
            for i, f in enumerate(tls_files):
                # Calculate estimated time to avoid 10,000 exif calls
                ts = t0 + timedelta(seconds=i * 10)
                self.frames.append((ts, os.path.join(root, f)))
        
        self.frames.sort()
        print(f"Indexed {len(self.frames)} frames.")

    def get_range(self, target_time, duration_sec=60, center_ratio=0.5):
        num_frames = duration_sec * 30 # 30 fps
        frames_before = int(num_frames * center_ratio)
        
        # Find closest frame to target
        self.frames.sort() # Ensure sorted
        
        # Quick binary search for target_time
        import bisect
        idx = bisect.bisect_left(self.frames, (target_time, ""))
        
        start_idx = max(0, idx - frames_before)
        end_idx = start_idx + num_frames
        
        if end_idx > len(self.frames):
            end_idx = len(self.frames)
            start_idx = max(0, end_idx - num_frames)
            
        return self.frames[start_idx:end_idx]

def create_overlapping_timelapse(frame_list, output_path):
    # Temp folder for symlinks
    tmp_dir = "tmp_frames"
    if os.path.exists(tmp_dir): subprocess.run(["rm", "-rf", tmp_dir])
    os.makedirs(tmp_dir)
    
    for i, (ts, path) in enumerate(frame_list):
        new_name = f"TLS_{i+1:09d}.jpg"
        os.symlink(path, os.path.join(tmp_dir, new_name))
    
    print(f"Creating video: {output_path}")
    cmd = ["./make_timelapse.sh", tmp_dir, output_path, "1", str(len(frame_list)), "30", "cw", "no"]
    subprocess.run(cmd)
    subprocess.run(["rm", "-rf", tmp_dir])

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python automate_overlapping_timelapse.py <root_dir> [output_dir]")
        sys.exit(1)
        
    root = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else "./output_overlapping"
    if not os.path.exists(out): os.makedirs(out)
    
    timeline = GlobalTimeline(root)
    
    # Range of dates in the timeline
    if not timeline.frames:
        print("No frames found.")
        sys.exit(0)
        
    start_date = timeline.frames[0][0].date()
    end_date = timeline.frames[-1][0].date()
    
    curr = start_date
    while curr <= end_date:
        for event in ["sunrise", "sunset"]:
            target = get_sun_time(datetime.combine(curr, datetime.min.time()), event)
            if not target: continue
            
            # Check if we have frames near this time
            if timeline.frames[0][0] <= target <= timeline.frames[-1][0]:
                # Request: sunrise shifted 15s back (in lapsed time)
                # If it was 0.45 (~27s in), shifting 15s back means the clip starts 15s earlier
                # 0.45 + (15/60) = 0.7
                ratio = 0.7 if event == "sunrise" else 0.5
                frames = timeline.get_range(target, center_ratio=ratio)
                
                if frames:
                    out_name = os.path.join(out, f"{curr.strftime('%Y-%m-%d')}_{event}.mp4")
                    if os.path.exists(out_name):
                        print(f"Skip: {out_name}")
                    else:
                        create_overlapping_timelapse(frames, out_name)
        
        curr += timedelta(days=1)
