"""Pick of the day videos for serving_quant: see render.py."""
from .pick import Pick


def render_pick_video(*args, **kwargs):
    """See picks.render.render_pick_video (imported lazily so `python -m picks.render` runs cleanly)."""
    from .render import render_pick_video as r
    return r(*args, **kwargs)
