"""The pick a video is about: what serving_quant hands over, as a dict or a JSON file.

Only `market`, `side` and `price_cents` are required; everything else is optional and simply not drawn
when missing. Example (examples/mock_pick.json):

    {
      "date": "2026-10-15",                       # the day the pick is posted (post text only)
      "exchange": "kalshi",                       # kalshi, polymarket_us or polymarket_global (exchanges.py)
      "market": "Highest temperature in NYC today",  # the question, as the exchange titles it
      "target_date": "2026-10-15",                # the day the market resolves on: "(Thu Oct 15)" goes
                                                  #   after "today" / "tomorrow" in the title
      "forecast_at": "2026-10-15T11:00:00Z",      # when the forecast was generated (UTC)
      "placed_at": "2026-10-15T11:07:00Z",        # when the bet was placed (UTC)
      "price_history": [["2026-10-15T09:00:00Z", 31], ...],  # optional, for the figure: price snapshots
                                                  #   up to the bet (5-minute database); synthetic if absent
      "outcome": "71° to 72°",                    # the contract / bracket being bought
      "side": "YES",                              # YES or NO
      "price_cents": 34,                          # entry price
      "model_prob": 0.52,                         # the model's probability for the side taken
      "record": {"wins": 41, "losses": 23},       # the picks' running record (or a string, "41-23")
      "drivers": ["Forecast blend 71.7°F", ...],  # up to 3 short lines from the blend details
      "ticker": "KXHIGHNY-26OCT15-B71.5",
      "handle": "@yourhandle",
      "disclaimer": "Not financial advice."
    }
"""
import json
import re
from datetime import date, datetime, timezone
from dataclasses import dataclass, field, fields
from typing import Optional

from . import exchanges


@dataclass
class Pick:
    market: str
    side: str
    price_cents: float
    outcome: str = ""
    exchange: str = ""
    date: str = ""
    event: str = ""
    target_date: str = ""
    forecast_at: str = ""
    placed_at: str = ""
    price_history: list = field(default_factory=list)
    model_prob: Optional[float] = None
    drivers: list = field(default_factory=list)
    record: object = None
    ticker: str = ""
    handle: str = ""
    disclaimer: str = "Not financial advice."

    @classmethod
    def load(cls, src):
        """A Pick, a dict, or a path to a JSON file."""
        if isinstance(src, cls): return src
        d = src if isinstance(src, dict) else json.load(open(src, encoding="utf-8"))
        known = {f.name for f in fields(cls)}
        p = cls(**{k: v for k, v in d.items() if k in known})
        p.side = p.side.upper()
        return p

    @property
    def edge_pts(self):
        """Model probability minus the price, in percentage points (None without a model probability)."""
        if self.model_prob is None: return None
        return round(self.model_prob * 100 - self.price_cents)

    def record_text(self):
        """'41–23 · 64%' (pushes shown as a third number when there are any); '' without a record."""
        r = self.record
        if not r: return ""
        if isinstance(r, str): return r.replace("-", "–")
        w, l, pu = r.get("wins", 0), r.get("losses", 0), r.get("pushes", 0)
        s = f"{w}–{l}" + (f"–{pu}" if pu else "")
        return s + (f" · {100 * w / (w + l):.0f}%" if w + l else "")

    @property
    def exch(self):
        return exchanges.get(self.exchange)

    @property
    def side_label(self):
        return self.exch["sides"].get(self.side, self.side)

    def title(self):
        """The market with the target date in parentheses after "today" / "tomorrow" (or at the end)."""
        if not self.target_date: return self.market + (f" · {self.event}" if self.event else "")
        d = date.fromisoformat(self.target_date[:10])
        tag = f"({d:%a %b} {d.day})"
        out, n = re.subn(r"\b(today|tomorrow|tonight)\b", lambda m: f"{m.group(0)} {tag}", self.market,
                         count=1, flags=re.I)
        return out if n else f"{self.market} {tag}"

    def times_line(self):
        """'Forecast Oct 15 11:00 UTC · Bet 11:07 UTC' (the bet's date only when it differs)."""
        f, b = utc(self.forecast_at), utc(self.placed_at)
        parts = []
        if f: parts.append(f"Forecast {stamp(f)}")
        if b: parts.append(f"Bet {stamp(b, with_date=not f or f.date() != b.date())}")
        return " · ".join(parts)

    def headline(self):
        """'YES 71° to 72° @ 34¢'"""
        return " ".join(x for x in (self.side_label, self.outcome) if x) + f" @ {self.price_cents:g}¢"

    def post_caption(self):
        """Plain text for the social post itself (the clip's description)."""
        lines = [f"Pick of the day{' · ' + self.date if self.date else ''}", self.title(), self.headline()]
        if self.times_line(): lines.append(self.times_line())
        if self.record_text(): lines.append(f"Picks record: {self.record_text()}")
        if self.model_prob is not None:
            lines.append(f"Model {self.model_prob * 100:.0f}% vs market {self.price_cents:g}% (edge {self.edge_pts:+d} pts)")
        lines += [f"· {d}" for d in self.drivers[:3]]
        if self.disclaimer: lines.append(self.disclaimer)
        return "\n".join(lines)


def utc(iso):
    """An ISO time ('...Z', '+00:00' or naive, taken as UTC) as an aware UTC datetime; None if empty."""
    if not iso: return None
    t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def stamp(t, with_date=True):
    return (f"{t:%b} {t.day} " if with_date else "") + f"{t:%H:%M} UTC"
