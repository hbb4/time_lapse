"""Sun elevation and azimuth (NOAA solar position equations, about 0.1 degree accuracy)."""

import math
from datetime import datetime, timezone

from .config import LATITUDE, LONGITUDE


def sun_position(when_utc: datetime, lat=LATITUDE, lon=LONGITUDE):
    """Return (elevation_deg, azimuth_deg) for an aware datetime. Azimuth is clockwise from north."""
    t = when_utc.astimezone(timezone.utc)
    jd = t.timestamp() / 86400.0 + 2440587.5
    jc = (jd - 2451545.0) / 36525.0

    l0 = (280.46646 + jc * (36000.76983 + jc * 0.0003032)) % 360
    m = 357.52911 + jc * (35999.05029 - 0.0001537 * jc)
    e = 0.016708634 - jc * (0.000042037 + 0.0000001267 * jc)
    mr = math.radians(m)
    c = (math.sin(mr) * (1.914602 - jc * (0.004817 + 0.000014 * jc))
         + math.sin(2 * mr) * (0.019993 - 0.000101 * jc) + math.sin(3 * mr) * 0.000289)
    true_long = l0 + c
    omega = 125.04 - 1934.136 * jc
    app_long = true_long - 0.00569 - 0.00478 * math.sin(math.radians(omega))
    eps0 = 23 + (26 + (21.448 - jc * (46.815 + jc * (0.00059 - jc * 0.001813))) / 60) / 60
    eps = eps0 + 0.00256 * math.cos(math.radians(omega))
    decl = math.asin(math.sin(math.radians(eps)) * math.sin(math.radians(app_long)))

    y = math.tan(math.radians(eps / 2)) ** 2
    l0r = math.radians(l0)
    eqtime = 4 * math.degrees(
        y * math.sin(2 * l0r) - 2 * e * math.sin(mr) + 4 * e * y * math.sin(mr) * math.cos(2 * l0r)
        - 0.5 * y * y * math.sin(4 * l0r) - 1.25 * e * e * math.sin(2 * mr))

    minutes = t.hour * 60 + t.minute + t.second / 60 + t.microsecond / 6e7
    tst = (minutes + eqtime + 4 * lon) % 1440
    ha = tst / 4 - 180
    latr, har = math.radians(lat), math.radians(ha)
    cos_zen = math.sin(latr) * math.sin(decl) + math.cos(latr) * math.cos(decl) * math.cos(har)
    zen = math.acos(max(-1.0, min(1.0, cos_zen)))
    elev = 90 - math.degrees(zen)
    az = math.degrees(math.atan2(math.sin(har),
                                 math.cos(har) * math.sin(latr) - math.tan(decl) * math.cos(latr))) + 180
    return elev, az % 360


def sun_events(day, tz):
    """Local times of the sun events used by clip recipes on one calendar day.

    Keys: dawn (civil, -6 deg rising), sunrise (-0.833 rising), noon (highest),
    sunset (-0.833 setting), dusk (civil, -6 deg setting). Missing if the sun
    never crosses that angle that day (never the case in San Francisco).
    """
    from datetime import datetime, timedelta

    start = datetime(day.year, day.month, day.day, tzinfo=tz)
    step = timedelta(minutes=10)
    times = [start + i * step for i in range(24 * 6 + 1)]
    elev = [sun_position(t)[0] for t in times]

    def refine(a, b, angle):
        for _ in range(30):  # bisection to well under a second
            mid = a + (b - a) / 2
            if (sun_position(mid)[0] - angle) * (sun_position(a)[0] - angle) <= 0:
                b = mid
            else:
                a = mid
        return a + (b - a) / 2

    events = {}
    for name, angle, rising in (("dawn", -6, True), ("sunrise", -0.833, True),
                                ("sunset", -0.833, False), ("dusk", -6, False)):
        for i in range(len(times) - 1):
            e0, e1 = elev[i] - angle, elev[i + 1] - angle
            if (rising and e0 < 0 <= e1) or (not rising and e0 >= 0 > e1):
                events[name] = refine(times[i], times[i + 1], angle)
                break
    i = max(range(len(elev)), key=elev.__getitem__)
    lo, hi = times[max(i - 1, 0)], times[min(i + 1, len(times) - 1)]
    for _ in range(30):  # ternary search for the peak
        m1, m2 = lo + (hi - lo) / 3, lo + 2 * (hi - lo) / 3
        if sun_position(m1)[0] < sun_position(m2)[0]:
            lo = m1
        else:
            hi = m2
    events["noon"] = lo + (hi - lo) / 2
    return events
