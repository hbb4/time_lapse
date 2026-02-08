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

def get_sun_time(date, zenith_deg=90.833):
    """Calculates sun event time for SF based on zenith."""
    day_of_year = date.timetuple().tm_yday
    gamma = (2 * math.pi / 365.0) * (day_of_year - 1)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(gamma) - 0.032077 * math.sin(gamma) \
             - 0.014615 * math.cos(2 * gamma) - 0.040849 * math.sin(2 * gamma))
    decl = 0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma) \
           - 0.006758 * math.cos(2 * gamma) + 0.000907 * math.sin(2 * gamma) \
           - 0.002697 * math.cos(3 * gamma) + 0.00148 * math.sin(3 * gamma)
    rad_lat = math.radians(LATITUDE)
    zenith = math.radians(zenith_deg)
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
        print("Indexing drive for Rewind effect...")
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
        return [f for f in self.frames if start_time <= f[0] <= end_time]

def create_video_with_rewind(frame_list, output_path):
    # Rewind logic: Take original list + a subset of it in reverse
    # We want the rewind to be approx 1.5 seconds at 30fps = 45 frames
    N = len(frame_list)
    M = 45
    step = max(1, N // M)
    rewind_frames = frame_list[::-step]
    
    # Combined list
    full_list = frame_list + rewind_frames
    
    tmp_dir = "tmp_rewind"
    if os.path.exists(tmp_dir): subprocess.run(["rm", "-rf", tmp_dir])
    os.makedirs(tmp_dir)
    
    print(f"Processing {len(full_list)} frames (Original: {len(frame_list)}, Rewind: {len(rewind_frames)})")
    
    font = None
    for p in ["/Library/Fonts/Arial.ttf", "/System/Library/Fonts/Helvetica.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]:
        if os.path.exists(p):
            try: font = ImageFont.truetype(p, 40); break
            except: continue
            
    for i, (ts, path) in enumerate(full_list):
        img = Image.open(path)
        if img.width > img.height:
            img = img.transpose(Image.ROTATE_270)
        
        draw = ImageDraw.Draw(img)
        ts_str = ts.strftime("%Y-%m-%d %H:%M:%S")
        draw.text((img.width - 450, img.height - 80), ts_str, font=font, fill=(200, 200, 200, 150) if font else (200,200,200))
        img.save(os.path.join(tmp_dir, f"frame_{i+1:09d}.jpg"), quality=90)
    
    print(f"Encoding video with rewind: {output_path}")
    subprocess.run(["ffmpeg", "-loglevel", "quiet", "-y", "-framerate", "30", "-i", os.path.join(tmp_dir, "frame_%09d.jpg"), "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", output_path])
    subprocess.run(["rm", "-rf", tmp_dir])

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python automate_rewind.py <root_dir> [output_dir]")
        sys.exit(1)
    root, out = sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "./output_rewind"
    if not os.path.exists(out): os.makedirs(out)
    
    # Test case: Dec 15, 2025
    TEST_DAY = datetime(2025, 12, 15)
    CUTOFF = SF_TZ.localize(TEST_DAY - timedelta(days=2))
    timeline = GlobalTimeline(root, start_cutoff=CUTOFF)
    
    sunset = get_sun_time(TEST_DAY, 90.833)
    nautical_dusk = get_sun_time(TEST_DAY, 102.0)
    
    if sunset and nautical_dusk:
        # Dynamic Window Calculation:
        # Start: 2.5 hours before sunset, but 5 PM at the latest.
        # End: Exactly Nautical Dusk (when last light goes away).
        
        s_win = sunset - timedelta(minutes=150)
        target_5pm = SF_TZ.localize(datetime(TEST_DAY.year, TEST_DAY.month, TEST_DAY.day, 17, 0, 0))
        
        final_start = min(s_win, target_5pm)
        final_end = nautical_dusk
        
        frames = timeline.get_time_window(final_start, final_end)
        if frames:
            out_name = os.path.join(out, f"2025-12-15_goldenhr_rewind.mp4")
            create_video_with_rewind(frames, out_name)
            print(f"Created rewind video: {out_name}")
        else:
            print("No frames found for Dec 15 window.")
