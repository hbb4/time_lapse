"""Draw a stand-in position figure from a pick, for testing the video without the real one.

It is a generic, unbranded order-ticket look with "MOCK" in the corner. The price line runs up to the
moment the bet was placed, ends at a dashed vertical line, and the bet time (UTC) sits at the lower right.
It plots the pick's "price_history" when given (snapshots up to the bet), else a made-up line. The real
figure will be drawn from the 5-minute price snapshots database on the cloud machine; any size works.

    python -m picks.mock_screenshot picks/examples/mock_pick.json mock_position.png
"""
import argparse
from PIL import Image, ImageDraw

from .pick import Pick, stamp, utc
from .render import font

BG, INK, MUTED, LINE = (250, 250, 252), (20, 24, 31), (118, 124, 135), (226, 229, 235)
GREEN, RED = (0, 168, 107), (226, 68, 68)


def draw(pick, out, w=1179, h=1060):
    p = Pick.load(pick)
    im = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(im)
    f = lambda role, s: font(role, s)
    x = 64
    ex = p.exch
    d.text((x, 56), (ex["name"] or "Exchange").upper(), font=f("bold", 34), fill=MUTED)
    d.text((w - 64, 56), "MOCK", font=f("bold", 34), fill=(200, 120, 0), anchor="ra")
    tf = 58
    while font("heavy", tf).getlength(p.title()) > w - 2 * x and tf > 34: tf -= 2
    d.text((x, 120), p.title(), font=f("heavy", tf), fill=INK)
    d.line((x, 230, w - x, 230), fill=LINE, width=3)
    col = GREEN if p.side == "YES" else RED
    d.text((x, 270), p.outcome or p.market, font=f("bold", 52), fill=INK)
    d.rounded_rectangle((w - x - 210, 260, w - x, 340), 20, fill=col)
    d.text((w - x - 105, 300), p.side_label, font=f("heavy", 46), fill=(255, 255, 255), anchor="mm")
    cur = "$" if ex["settles"] == "USD" else ""
    unit = "" if cur else f" {ex['settles']}"
    rows = [("Avg price", f"{p.price_cents:g}¢"), (ex["lot"], "100"),
            ("Cost", f"{cur}{p.price_cents:.2f}{unit}"), ("Payout if right", f"{cur}100.00{unit}")]
    y = 390
    for k, v in rows:
        d.text((x, y), k, font=f("light", 42), fill=MUTED)
        d.text((w - x, y), v, font=f("bold", 42), fill=INK, anchor="ra")
        y += 80
    d.line((x, y + 6, w - x, y + 6), fill=LINE, width=3)

    # price line up to the bet, ending at a dashed vertical line
    top, bottom = y + 40, h - 110
    if p.price_history:
        ys = [float(c) for _, c in p.price_history]
    else:
        import math
        ys = [p.price_cents * (0.85 + 0.12 * math.sin(i / 7) + 0.03 * math.sin(i * 1.7)) for i in range(59)]
        ys.append(p.price_cents)
    lo, hi = min(ys), max(ys)
    pad = max(1.0, (hi - lo) * 0.15); lo, hi = lo - pad, hi + pad
    xe = w - x - 6
    pts = [(x + i * (xe - x) / max(1, len(ys) - 1), bottom - (v - lo) / (hi - lo) * (bottom - top)) for i, v in enumerate(ys)]
    d.line(pts, fill=col, width=6, joint="curve")
    for yy in range(int(top) - 10, int(bottom) + 10, 22):
        d.line((xe, yy, xe, yy + 12), fill=MUTED, width=3)
    d.ellipse((pts[-1][0] - 10, pts[-1][1] - 10, pts[-1][0] + 10, pts[-1][1] + 10), fill=col)
    placed = utc(p.placed_at)
    if placed: d.text((w - x, h - 60), f"Bet placed {stamp(placed)}", font=f("bold", 32), fill=MUTED, anchor="rs")
    im.save(out)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pick"); ap.add_argument("out")
    a = ap.parse_args()
    print(draw(a.pick, a.out))
