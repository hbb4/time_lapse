"""Per-exchange wording for the pick video and the position figure.

A pick names its exchange with "exchange": a key below ("kalshi", "polymarket_us", "polymarket_global")
or the display name ("Kalshi", "Polymarket US", ...), any case. Anything else is shown as given, with
Kalshi-style wording.

    name      shown in the header line and on the figure
    sides     how YES / NO are written on that exchange
    lot       what one position unit is called
    settles   what a winning unit pays out in (one per unit)
"""

EXCHANGES = {
    "kalshi":            dict(name="Kalshi", sides={"YES": "YES", "NO": "NO"}, lot="Contracts", settles="USD"),
    "polymarket_us":     dict(name="Polymarket US", sides={"YES": "Yes", "NO": "No"}, lot="Shares", settles="USD"),
    "polymarket_global": dict(name="Polymarket", sides={"YES": "Yes", "NO": "No"}, lot="Shares", settles="USDC"),
}


def get(name):
    """The config for a key or display name; unknown names get Kalshi wording under their own name."""
    k = (name or "").strip().lower().replace(" ", "_").replace("-", "_")
    if k in EXCHANGES: return dict(EXCHANGES[k], key=k)
    for key, c in EXCHANGES.items():
        if c["name"].lower() == (name or "").strip().lower(): return dict(c, key=key)
    return dict(EXCHANGES["kalshi"], name=name or "", key="")
