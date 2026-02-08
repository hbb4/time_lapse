import os
import sys
import subprocess
from datetime import datetime, timedelta

import math

# Coordinates provided by user
LATITUDE = 37.791667734079596
LONGITUDE = -122.41549323195979

def get_sun_time(date, event="sunrise"):
    """
    Calculates sunrise or sunset for a given date using the 
    General Solar Position algorithm.
    """
    # Latitude and longitude in degrees
    lat = LATITUDE
    lng = LONGITUDE
    
    # Day of year
    day_of_year = date.timetuple().tm_yday
    
    # 1. first calculate the fractional year
    gamma = (2 * math.pi / 365.0) * (day_of_year - 1 + (12.0 - 12.0) / 24.0)
    
    # 2. estimate equation of time and solar declination
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(gamma) - 0.032077 * math.sin(gamma) \
             - 0.014615 * math.cos(2 * gamma) - 0.040849 * math.sin(2 * gamma))
    
    decl = 0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma) \
           - 0.006758 * math.cos(2 * gamma) + 0.000907 * math.sin(2 * gamma) \
           - 0.002697 * math.cos(3 * gamma) + 0.00148 * math.sin(3 * gamma)
    
    # 3. calculate the hour angle
    # Zenith for sunrise/sunset is usually 90.833 degrees
    zenith = math.radians(90.833)
    rad_lat = math.radians(lat)
    
    # check for atmospheric refraction 
    cos_ha = (math.cos(zenith) / (math.cos(rad_lat) * math.cos(decl))) - (math.tan(rad_lat) * math.tan(decl))
    
    if cos_ha > 1: # Always night
        return None
    if cos_ha < -1: # Always day
        return None
        
    ha = math.acos(cos_ha)
    if event == "sunrise":
        ha = -ha
    
    # 4. calculate UTC time in minutes
    # Assuming GMT-8 for San Francisco (standard time)
    # We'll calculate UTC and then adjust to date's local offset
    ha_deg = math.degrees(ha)
    
    # solar_noon_utc = 720 - (4 * longitude) - eqtime
    solar_noon_utc = 720 - (4 * lng) - eqtime
    
    # Calculate both times and sort them to be absolutely sure
    t1 = solar_noon_utc - (4 * ha_deg)
    t2 = solar_noon_utc + (4 * ha_deg)
    
    morning_utc, evening_utc = min(t1, t2), max(t1, t2)
    utctime = morning_utc if event == "sunrise" else evening_utc
    
    # Basic PST offset (GMT-8)
    local_time_min = utctime - (8 * 60) 
    
    # Handle day wrap-around
    local_time_min = local_time_min % 1440
    
    l_hours = int(local_time_min // 60)
    l_minutes = int(local_time_min % 60)
    
    return datetime(date.year, date.month, date.day, l_hours, l_minutes)

def get_exif_timestamp(filepath):
    """Uses exiftool to get the DateTimeOriginal or CreateDate."""
    cmd = ["exiftool", "-DateTimeOriginal", "-CreateDate", "-d", "%Y-%m-%d %H:%M:%S", "-s3", filepath]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0 and result.stdout.strip():
        # Get the first line of output
        return datetime.strptime(result.stdout.strip().split('\n')[0], "%Y-%m-%d %H:%M:%S")
    return None

def find_frames_for_event(folder, target_time, duration_seconds=60, fps=30, interval_seconds=10, center_ratio=0.5):
    """
    Finds frame range for an event.
    center_ratio: 0.5 means event is in middle. 0.2 means event is 20% into the clip.
    """
    first_frame_path = os.path.join(folder, "TLS_000000001.jpg")
    if not os.path.exists(first_frame_path):
        return None, None
    
    t0 = get_exif_timestamp(first_frame_path)
    if not t0:
        print(f"Warning: Could not get EXIF from {first_frame_path}, using file mtime.")
        t0 = datetime.fromtimestamp(os.path.getmtime(first_frame_path))
    
    # How many frames is the target?
    target_offset_seconds = (target_time - t0).total_seconds()
    target_frame = int(target_offset_seconds / interval_seconds) + 1
    
    # 60 seconds at 30fps = 1800 frames
    num_frames = duration_seconds * fps
    
    # Calculate start frame based on ratio
    # If ratio is 0.2 (20%), then 20% of frames should be before the target
    frames_before = int(num_frames * center_ratio)
    start_frame = max(1, target_frame - frames_before)
    end_frame = start_frame + num_frames
    
    return start_frame, end_frame

def process_folder(folder, output_dir):
    """Detects sunrise/sunset in a folder and creates videos."""
    # Check first and last frame to see the time range
    first_frame = os.path.join(folder, "TLS_000000001.jpg")
    if not os.path.exists(first_frame): return
    
    # Get last frame number
    try:
        all_files = sorted([f for f in os.listdir(folder) if f.startswith("TLS_") and f.endswith(".jpg")])
    except OSError:
        return
        
    if not all_files: return
    last_frame_file = all_files[-1]
    
    t_start = get_exif_timestamp(first_frame)
    t_end = get_exif_timestamp(os.path.join(folder, last_frame_file))
    
    if not t_start or not t_end:
        print(f"Warning: Could not get timestamps for {folder}")
        return
    
    print(f"Processing {folder}")
    print(f"Range: {t_start} to {t_end}")
    
    # Check for sunrise/sunset on each day in range
    current_date = t_start.date()
    end_dt_date = t_end.date()
    
    while current_date <= end_dt_date:
        for event in ["sunrise", "sunset"]:
            sun_time = get_sun_time(datetime.combine(current_date, datetime.min.time()), event)
            
            if sun_time is None:
                continue

            # buffer of 2 hours for range check to be safe
            if t_start - timedelta(hours=2) <= sun_time <= t_end + timedelta(hours=2):
                # Request: sunrise relatively early but shifted 15s back from 0.2 (0.2 + 15/60 = 0.45)
                # sunset stays centered (~50% in)
                ratio = 0.45 if event == "sunrise" else 0.5
                s, e = find_frames_for_event(folder, sun_time, center_ratio=ratio)
                
                if s is not None and e is not None:
                    # Check if frames exist in the folder's range
                    if s < len(all_files) and e > 1:
                        date_str = current_date.strftime("%Y-%m-%d")
                        output_name = f"{date_str}_{event}.mp4"
                        output_path = os.path.join(output_dir, output_name)
                        
                        if os.path.exists(output_path):
                            print(f"Skip: {output_name} already exists")
                            continue
                            
                        print(f"Found {event} on {date_str} at {sun_time.strftime('%H:%M')}. Range: {s}-{e} (ratio: {ratio})")
                        
                        # Set SHOW_TIMESTAMP="no" because current ffmpeg lacks drawtext
                        cmd = ["./make_timelapse.sh", folder, output_path, str(s), str(e), "30", "cw", "no"]
                        subprocess.run(cmd)
                    
        current_date += timedelta(days=1)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python automate_timelapse.py <input_folder_or_parent> [output_dir]")
        sys.exit(1)
        
    input_path = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else "."
    
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        
    if os.path.isdir(input_path):
        # Check if it's a frames folder or a parent folder
        if any(f.startswith("TLS_") for f in os.listdir(input_path)):
            process_folder(input_path, output_dir)
        else:
            # Sort subfolders by name descending (most recent first)
            subfolders = sorted([os.path.join(input_path, d) for d in os.listdir(input_path) 
                                if os.path.isdir(os.path.join(input_path, d))], reverse=True)
            for sub in subfolders:
                process_folder(sub, output_dir)

