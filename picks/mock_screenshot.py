"""Draw a stand-in position screenshot from a pick, for testing the video without a real exchange capture.

It is a generic, unbranded order-ticket look with "MOCK" in the corner; the real input is a screenshot
from the exchange (any size; the renderer fits it).

    python -m picks.mock_screenshot picks/examples/mock_pick.json mock_position.png
"""
import argparse
from PIL import Image, ImageDraw

from .pick import Pick
from .render import font

BG, INK, MUTED, LINE = (250, 250, 252), (20, 24, 31), (118, 124, 135), (226, 229, 235)
GREEN, RED = (0, 168, 107), (226, 68, 68)


def draw(pick, out, w=1179, h=1060):
    p = Pick.load(pick)
    im = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(im)
    f = lambda role, s: font(role, s)
    x = 64
    d.text((x, 56), (p.exchange or "Exchange").upper(), font=f("bold", 34), fill=MUTED)
    d.text((w - 64, 56), "MOCK", font=f("bold", 34), fill=(200, 120, 0), anchor="ra")
    d.text((x, 120), p.market, font=f("heavy", 58), fill=INK)
    if p.event: d.text((x, 196), p.event, font=f("light", 40), fill=MUTED)
    d.line((x, 270, w - x, 270), fill=LINE, width=3)
    col = GREEN if p.side == "YES" else RED
    d.text((x, 310), p.outcome or p.market, font=f("bold", 52), fill=INK)
    d.rounded_rectangle((w - x - 210, 300, w - x, 380), 20, fill=col)
    d.text((w - x - 105, 340), p.side, font=f("heavy", 46), fill=(255, 255, 255), anchor="mm")
    rows = [("Avg price", f"{p.price_cents:g}¢"), ("Contracts", "100"),
            ("Cost", f"${p.price_cents:.2f}"), ("Payout if right", "$100.00")]
    y = 440
    for k, v in rows:
        d.text((x, y), k, font=f("light", 42), fill=MUTED)
        d.text((w - x, y), v, font=f("bold", 42), fill=INK, anchor="ra")
        y += 84
    d.line((x, y + 10, w - x, y + 10), fill=LINE, width=3)
    # a small price line, the kind of chart an order ticket shows
    import math
    pts = [(x + i * (w - 2 * x) / 59, y + 190 - 70 * (0.5 + 0.4 * math.sin(i / 7) + 0.1 * math.sin(i * 1.7)))
           for i in range(60)]
    d.line(pts, fill=col, width=6, joint="curve")
    d.text((x, h - 70), p.ticker, font=f("mono", 32), fill=MUTED)
    im.save(out)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pick"); ap.add_argument("out")
    a = ap.parse_args()
    print(draw(a.pick, a.out))
