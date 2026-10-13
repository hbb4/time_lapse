import math
from datetime import datetime, timedelta
import pytz

LATITUDE = 37.791667734079596
LONGITUDE = -122.41549323195979
SF_TZ = pytz.timezone("America/Los_Angeles")

def get_sun_events(date):
    day_of_year = date.timetuple().tm_yday
    gamma = (2 * math.pi / 365.0) * (day_of_year - 1)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(gamma) - 0.032077 * math.sin(gamma) - 0.014615 * math.cos(2 * gamma) - 0.040849 * math.sin(2 * gamma))
    decl = 0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma) - 0.006758 * math.cos(2 * gamma) + 0.000907 * math.sin(2 * gamma) - 0.002697 * math.cos(3 * gamma) + 0.00148 * math.sin(3 * gamma)
    rad_lat = math.radians(LATITUDE)

    def time_for_zenith(z_deg):
        zenith = math.radians(z_deg)
        cos_ha = (math.cos(zenith) / (math.cos(rad_lat) * math.cos(decl))) - (math.tan(rad_lat) * math.tan(decl))
        if cos_ha > 1 or cos_ha < -1: return None
        ha_deg = math.degrees(math.acos(cos_ha))
        solar_noon_utc = 720 - (4 * LONGITUDE) - eqtime
        utctime = solar_noon_utc + (4 * ha_deg) # Sunset side
        dt_utc = datetime(date.year, date.month, date.day, tzinfo=pytz.UTC) + timedelta(minutes=utctime)
        return dt_utc.astimezone(SF_TZ)
    
    return time_for_zenith(90.833) # Sunset

d = datetime(2025, 12, 15)
sunset = get_sun_events(d)
print(f"Sunset on Dec 15: {sunset}")
print(f"3:30 PM is: {sunset - timedelta(minutes=81)}") # 16:51 - 81 = 15:30
print(f"6:00 PM is: {sunset + timedelta(minutes=69)}") # 16:51 + 69 = 18:00
