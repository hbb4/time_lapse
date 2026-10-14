"""The pick a video is about: what serving_quant hands over, as a dict or a JSON file.

Only `market`, `side` and `price_cents` are required; everything else is optional and simply not drawn
when missing. Example (examples/mock_pick.json):

    {
      "date": "2026-10-15",                       # the day the pick is posted (header line)
      "exchange": "Kalshi",
      "market": "Highest temperature in NYC",     # the question, as the exchange titles it
      "event": "Thu Oct 15",                      # optional sub-line under the market
      "outcome": "71° to 72°",                    # the contract / bracket being bought
      "side": "YES",                              # YES or NO
      "price_cents": 34,                          # entry price
      "model_prob": 0.52,                         # the model's probability for the side taken
      "drivers": ["Forecast blend 71.7°F", ...],  # up to 3 short lines from the blend details
      "ticker": "KXHIGHNY-26OCT15-B71.5",
      "handle": "@yourhandle",
      "disclaimer": "Not financial advice."
    }
"""
import json
from dataclasses import dataclass, field, fields
from typing import Optional


@dataclass
class Pick:
    market: str
    side: str
    price_cents: float
    outcome: str = ""
    exchange: str = ""
    date: str = ""
    event: str = ""
    model_prob: Optional[float] = None
    drivers: list = field(default_factory=list)
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

    def headline(self):
        """'YES 71° to 72° @ 34¢'"""
        return " ".join(x for x in (self.side, self.outcome) if x) + f" @ {self.price_cents:g}¢"

    def post_caption(self):
        """Plain text for the social post itself (the clip's description)."""
        lines = [f"Pick of the day{' · ' + self.date if self.date else ''}",
                 f"{self.market}{' (' + self.event + ')' if self.event else ''}",
                 self.headline()]
        if self.model_prob is not None:
            lines.append(f"Model {self.model_prob * 100:.0f}% vs market {self.price_cents:g}% (edge {self.edge_pts:+d} pts)")
        lines += [f"· {d}" for d in self.drivers[:3]]
        if self.disclaimer: lines.append(self.disclaimer)
        return "\n".join(lines)
