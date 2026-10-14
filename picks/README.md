# Pick of the day videos

A short vertical (9:16, 1080x1920) clip for a social post: the time lapse plays behind, and the day's
pick sits in front. No voice; an instrumental is optional.

On screen (15 s by default): PICK OF THE DAY, the date and market title, then the position screenshot
rises in large, then shrinks up while the side and price, model vs market vs edge, and up to three
forecast drivers come in on a frosted panel; it ends on the handle and the disclaimer. Text stays inside
the area TikTok, Reels and Shorts leave clear of their buttons.

## Run it

```
python -m picks.render PICK.json SCREENSHOT.png FRAMES OUT.mp4 [--music track.mp3 --music-start 0:12 --music-db -3]
```

- `PICK.json`: the pick (fields in `pick.py`; example in `examples/mock_pick.json`).
- `SCREENSHOT.png`: the position as captured from the exchange, any size.
- `FRAMES`: a text file of frame paths in order, or a folder of `TLS_*.jpg`. The whole list plays across
  the clip.
- Writes `OUT.mp4`, `OUT_cover.jpg` (thumbnail at the stats moment) and `OUT.txt` (text for the post).
- `--crf 16` by default (visually lossless x264), `--move push|pull|tilt|still|drift` for the camera.

From Python, e.g. inside serving_quant with this repo cloned alongside:

```python
import sys; sys.path.insert(0, "/path/to/time_lapse")
from picks import render_pick_video
video, cover, text = render_pick_video(pick_dict, "position.png", frame_paths, "out/pick.mp4", music=None)
```

No test screenshot handy? `python -m picks.mock_screenshot picks/examples/mock_pick.json mock.png` draws an
unbranded stand-in marked MOCK.

## Needs

Python with numpy and Pillow, ffmpeg on PATH. Fonts come from `captions/styles.py` (macOS system fonts,
else Inter / DejaVu on Linux, or drop your own in `captions/fonts/`). The background reuses
`captions/render.py` (deflicker, frame blending, camera moves), so `captions/` must be present.

## Open items

- The pick's fields are a guess until they are wired to serving_quant's pick and blend output.
- On the cloud machine the frames need to be reachable there (a synced set of days, or unpacked from the
  JPEG XL tars with `djxl`), plus a rule for which day and span to use behind each pick.
- Real exchange screenshots: captured by serving_quant (or by hand) and passed in as a file.
