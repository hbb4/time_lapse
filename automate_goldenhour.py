import os
import sys
import subprocess
import math
from datetime import datetime, timedelta
import pytz
from PIL import Image, ImageDraw, ImageFont

# San Francisco Coordinates
LATITUDE = 37.791667734079596
LONGITUDE = -122.41549323195979
SF_TZ = pytz.timezone("America/Los_Angeles")

def get_sunset_time(date):
    """Calculates sunset for SF."""
    day_of_year = date.timetuple().tm_yday
    gamma = (2 * math.pi / 365.0) * (day_of_year - 1)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(gamma) - 0.032077 * math.sin(gamma) \
             - 0.014615 * math.cos(2 * gamma) - 0.040849 * math.sin(2 * gamma))
    decl = 0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma) \
           - 0.006758 * math.cos(2 * gamma) + 0.000907 * math.sin(2 * gamma) \
           - 0.002697 * math.cos(3 * gamma) + 0.00148 * math.sin(3 * gamma)
    rad_lat = math.radians(LATITUDE)
    zenith = math.radians(90.833)
    cos_ha = (math.cos(zenith) / (math.cos(rad_lat) * math.cos(decl))) - (math.tan(rad_lat) * math.tan(decl))
    if cos_ha > 1 or cos_ha < -1: return None
    ha_deg = math.degrees(math.acos(cos_ha))
    solar_noon_utc = 720 - (4 * LONGITUDE) - eqtime
    utctime = solar_noon_utc + (4 * ha_deg)
    dt_utc = datetime(date.year, date.month, date.day, tzinfo=pytz.UTC) + timedelta(minutes=utctime)
    return dt_utc.astimezone(SF_TZ)

def get_exif_timestamp(filepath):
    cmd = ["exiftool", "-DateTimeOriginal", "-CreateDate", "-d", "%Y-%m-%d %H:%M:%S", "-s3", filepath]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0 and result.stdout.strip():
        return datetime.strptime(result.stdout.strip().split('\n')[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=SF_TZ)
    return None

class GlobalTimeline:
    def __init__(self, root_dir, start_cutoff=None):
        self.frames = []
        print("Indexing drive for Golden Hour...")
        for root, dirs, files in os.walk(root_dir):
            if "thumbnail" in root: continue
            tls_files = sorted([f for f in files if f.startswith("TLS_") and f.endswith(".jpg")])
            if not tls_files: continue
            t0 = get_exif_timestamp(os.path.join(root, tls_files[0]))
            if not t0: continue
            if start_cutoff and t0 < start_cutoff: continue
            tn = get_exif_timestamp(os.path.join(root, tls_files[-1]))
            if not tn: continue
            count = len(tls_files)
            interval = (tn - t0).total_seconds() / (count - 1) if count > 1 else 10
            for i, f in enumerate(tls_files):
                ts = t0 + timedelta(seconds=i * interval)
                self.frames.append((ts, os.path.join(root, f)))
        self.frames.sort(key=lambda x: x[0])
        print(f"Indexed {len(self.frames)} frames.")

    def get_time_window(self, start_time, end_time):
        """Returns all frames between start_time and end_time."""
        return [f for f in self.frames if start_time <= f[0] <= end_time]

def create_video_with_timestamps(frame_list, output_path):
    tmp_dir = "tmp_golden"
    if os.path.exists(tmp_dir): subprocess.run(["rm", "-rf", tmp_dir])
    os.makedirs(tmp_dir)
    print(f"Processing {len(frame_list)} Golden Hour frames...")
    font = None
    for p in ["/Library/Fonts/Arial.ttf", "/System/Library/Fonts/Helvetica.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]:
        if os.path.exists(p):
            try: font = ImageFont.truetype(p, 40); break
            except: continue
    for i, (ts, path) in enumerate(frame_list):
        img = Image.open(path)
        
        # Smart Orientation: If the image is landscape, rotate it to vertical.
        if img.width > img.height:
            img = img.transpose(Image.ROTATE_270)
        
        draw = ImageDraw.Draw(img)
        ts_str = ts.strftime("%Y-%m-%d %H:%M:%S")
        draw.text((img.width - 450, img.height - 80), ts_str, font=font, fill=(200, 200, 200, 150) if font else (200,200,200))
        img.save(os.path.join(tmp_dir, f"frame_{i+1:09d}.jpg"), quality=90)
    print(f"Encoding Golden Hour video...")
    subprocess.run(["ffmpeg", "-loglevel", "quiet", "-y", "-framerate", "30", "-i", os.path.join(tmp_dir, "frame_%09d.jpg"), "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", output_path])
    subprocess.run(["rm", "-rf", tmp_dir])

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python automate_goldenhour.py <root_dir> [output_dir]")
        sys.exit(1)
    root, out = sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "./output_golden"
    if not os.path.exists(out): os.makedirs(out)
    
    # Process all frames from September 23, 2025 onwards
    CUTOFF = SF_TZ.localize(datetime(2025, 9, 23))
    timeline = GlobalTimeline(root, start_cutoff=CUTOFF)
    
    # Process requested test dates
    test_dates = [
        datetime(2025, 9, 10),
        datetime(2025, 10, 20),
        datetime(2025, 11, 10),
        datetime(2025, 12, 15)
    ]
    
    for d in test_dates:
        sunset = get_sunset_time(d)
        if sunset:
            # Dynamic Window Calculation:
            # Base logic: Start 2.5 hours before, end 2 hours after
            # Constraint: Start at 5 PM at latest, end at 9 PM at latest
            
            s_win = sunset - timedelta(minutes=150)
            e_win = sunset + timedelta(minutes=120)
            
            # Capping: Corrected logic to ensure we respect your 5PM/9PM "at the latest" bounds
            target_5pm = SF_TZ.localize(datetime(d.year, d.month, d.day, 17, 0, 0))
            target_9pm = SF_TZ.localize(datetime(d.year, d.month, d.day, 21, 0, 0))
            
            final_start = min(s_win, target_5pm)
            final_end = min(e_win, target_9pm)
            
            frames = timeline.get_time_window(final_start, final_end)
            if frames:
                out_name = os.path.join(out, f"{d.strftime('%Y-%m-%d')}_goldenhr_sunset.mp4")
                # Overwrite test files to show new expanded window
                if os.path.exists(out_name): os.remove(out_name)
                    
                print(f"Creating dramatic sunset for {d.strftime('%b %d')}:")
                print(f"  Sunset: {sunset.strftime('%H:%M')}")
                print(f"  Window: {final_start.strftime('%H:%M')} -> {final_end.strftime('%H:%M')}")
                
                create_video_with_timestamps(frames, out_name)
            else:
                print(f"No frames found for {d.strftime('%Y-%m-%d')} window.")
