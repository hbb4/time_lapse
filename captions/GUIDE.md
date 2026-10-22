# Read-aloud caption clips: guide

You record yourself reading a passage. These scripts put the reading over a stretch of time-lapse
footage and show the words on screen in sync with your voice. Everything lives in `captions/` inside
the time_lapse repo. Run commands from the repo root with the project venv.

```
captions/
  GUIDE.md        this file
  passages/       one .txt per passage: the "formatted script" (text + directions, see the key below)
  recordings/     your readings (m4a / mp3 / wav), named to match the passage
  music/          instrumental tracks
  sfx/            your own sound effects (optional): gust.wav, fog.wav... replace the synthesized openers
  clips.csv       the clip sheet: one row per video
  match.py        pairs passages with footage and writes a clip sheet for you
  batch.py        renders every row of a clip sheet
  render.py       renders one clip (batch.py calls it; you can too)
  align.py        finds when each word is spoken in your recording
  markup.py       reads the formatted script
  styles.py       the caption looks
  intro.py        opening sound + screen effects (section 4b)
```

Finished clips go to `/Volumes/CORSAIR/ATLI_videos_and_stills/_captions/`.

## 1. Setup (once)

```
.venv/bin/pip install numpy pillow pocketsphinx
brew install ffmpeg jpeg-xl          # ffmpeg for video/audio, djxl to read the packed folders
```

The footage comes from the pipeline (`pipeline.db` on CORSAIR), so a day has to be analysed by the
pipeline before you can use it. See section 7 for more dates.

## 2. Make a clip yourself

1. **Pick a passage.** All 235 are in `poetry/captions.csv` (index, author, title, text, word count).
   `match.py` (section 6) writes each one to `captions/passages/p###.txt` for you, or copy the text into a
   new file yourself.
2. **Format it.** Add stress, caption spots, effects and settings to the text file, using the key in
   section 3. Leave the words themselves exactly as you'll read them.
3. **Record it.** Read the passage and save the recording in `captions/recordings/`, for example
   `p042.m4a`. A phone voice memo is fine. Read the words as written; the stress marks don't need to
   match anything special in your voice.
4. **Add a row to `clips.csv`.** At minimum: `name`, `passage`, `recording`, `from`, `to`. The other
   columns are optional (section 4).
5. **Check it, then render it:**
   ```
   .venv/bin/python captions/batch.py captions/clips.csv --only p042 --dry-run
   .venv/bin/python captions/batch.py captions/clips.csv --only p042
   ```
   The dry run prints how many frames the span covers and from when to when. The real run aligns your
   reading (once, saved in `_captions/work/`), unpacks the frames it needs, renders, and cleans up.

To redo a clip, delete its mp4 in `_captions/` and run again. Changing only the music levels? Run
render.py with `--remix` (it reuses the video and only redoes the audio).

### How the demos were made

The demos ran render.py directly on a list of frame paths:

```
.venv/bin/python captions/render.py frames.txt demo.mp4 \
    --words words.json --text captions/passages/fog_gathers.txt --voice reading.wav \
    --music captions/music/track01.mp3 --music-start 0:06 --trails 0.5 --beat-rewind --style whisper
```

`frames.txt` is one frame path per line (every frame, in order). batch.py makes that file for you from
the `from` / `to` times, so the same demo as a sheet row is the `fog_gathers` example in `clips.csv`
(footage Sep 21 2026, 17:45 to 20:35).

## 3. The key: writing a formatted script

A passage file is the text you'll read, with a few marks added. Line breaks and blank lines between
stanzas matter (they're how key lines are found), so keep the poem's layout.

| Mark | Example | What it does |
|---|---|---|
| `@name value` | `@style serif` | A clip setting, on its own line at the **top** of the file. Same names as the sheet columns (section 4). A setting here wins over the sheet. |
| `*word*` | `It *gathers* at` | **Stress.** While you say it, this word is the only one on screen, in the style's stress font and color. Stars around several words stress the whole run: `*red light.*` |
| `{spot}` | `{hills}hills,` | **Caption spot.** The phrase starting at this word is drawn on a surface in the scene, in perspective. Spots are listed below. |
| `[effect]` | `[stairs]And then,` | **Layout effect** for the line starting here (to the end of the line or sentence, up to 12 words): `stairs`, `cascade`, `stack`, `spread`. `[plain]` keeps a line from getting an automatic effect. |
| `\|` | `once. \| It` | Force a caption break here. |
| line break | | In verse, each line is a unit for effects. |
| blank line | | End of a stanza or paragraph. Stanza ends are favoured for effects. |

Marks can be combined on one word: `{sky}[stack]And`.

**Caption spots** (where `{spot}` puts the words):

| Spot | Where |
|---|---|
| `edge` | standing on the near roof's front edge |
| `front` | painted on the near roof's siding |
| `side` | the right wall of the building behind |
| `rooftop` | lying flat on the near roof |
| `yellow` | the face of the yellow building mid-frame |
| `tan` | over the windows of the tall tan building on the left |
| `hills` | small, along the far ridge behind the skyline |
| `sky` | floating flat, high in the open sky |
| `clouds` | on the cloud ceiling, receding toward the horizon |

In 16:9, spots outside the crop (the near roof ones, sky, clouds) fall back to normal captions.

**Layout effects:**

- `stairs`: each word (or pair) steps down and starts where the last one ended.
- `cascade`: a few short lines, each indented a step further.
- `stack`: two to four short lines, each scaled to the same width (poster look).
- `spread`: one line with wide word spacing.

Without any marks, effects are placed automatically: candidate lines (a verse line, or in prose a
sentence or clause, 2 to 8 words, no stress or spot) are scored (closing line, stanza end, ends in ! or
?, short and punchy) and spread through the clip, about one every 10 s in 9:16 and 16 s in 16:9, never
two closer than half that. Change the rate with `@fx_every 6`, or `@fx_every 0` for only the lines you
mark. `@effects off` turns the automatic ones off.

**Example** (`captions/passages/fog_gathers.txt`):

```
@style serif
@move push
@aspect 9x16

The fog does not arrive all at once. It *gathers* at the edges of the {hills}hills, {rooftop}slowly,
the way a thought *gathers,* until the tower is only a rumor of red light. [stairs]And then, without
asking anyone, the evening lets it go.
```

## 4. Settings (sheet columns and @ lines)

| Name | Values | Default | What it does |
|---|---|---|---|
| `name` | text | | output file name (`name_9x16.mp4`) |
| `passage` | path | | the formatted script |
| `recording` | path | | your reading; the row is skipped until the file exists |
| `from`, `to` | `2026-09-21 17:45`, or `17:45` with a folder, or `#380` frame numbers with a folder | | the footage; date-times can span days and folders. Times are the pipeline's clock-corrected ones. |
| `folder` | `TLST05B01403_260921165611` (`+` joins several) | | only needed with clock times or frame numbers |
| `aspect` | `9x16`, `16x9`, `both` | `9x16` | 16x9 is the long-form look: smaller, wider caption lines, lower in the frame, framed high and on the right (sky and sunsets, not the big building) |
| `fit` | `span`, `realtime` | `span` | span: the whole footage span plays across the reading; realtime: every frame once at 30 fps (long readings cover more real time). Either way each video frame is one whole camera frame, never a blend: a span with more frames than the reading needs skips whole frames, and one with too few is widened evenly on both sides by batch.py so no frame repeats. |
| `blend` | `yes` / `no` | `no` | yes brings back blended frames (smoother at high speed, softer clouds) |
| `move` | `push`, `pull`, `tilt`, `still`, `drift` | `push` | slow camera move over the whole clip (drift: slow glide to the right, made for 16:9) |
| `style` | `pop`, `serif`, `type`, `whisper`, `poster` | random per clip | caption look (section 5) |
| `fx_every` | seconds | 10 (16 in 16:9) | about how often a layout effect appears; 0 = only marked lines |
| `ending` | `rewind`, `hold` | `rewind` | the rewind starts as the last word closes |
| `music` | path | none | instrumental track |
| `music_start` | `1:23.5` | `0` | where in the song to start |
| `music_db` | dB | `-16` | steady music level under the voice |
| `end_boost_db` | dB | `6` | how much louder the music gets after the reading |
| `trails` | 0 to 1 | `0` | light trails (best on clear nights) |
| `exposure` | number | `1` | long-exposure blur (blends frames on purpose; 1 = off) |
| `beat_punch` | e.g. `0.03` | `0` | zoom bump on downbeats (off by default) |
| `beat_rewind` | `yes` / `no` | `no` | nudge the rewind onto the nearest downbeat (by a fraction of a second at most) |
| `end_card` | path to an image, GIF or video | none (off) | shown after the ending, e.g. an author/title card. Make it in the clip's shape (1080x1920 for 9:16, 1920x1080 for 16:9); anything else is scaled to fill and cropped. It dissolves in from the last frame. Write `{aspect}` in the name (`cards/fog_{aspect}.png`) to use `fog_9x16.png` and `fog_16x9.png` with `aspect both`. |
| `end_card_seconds` | seconds | images 3, GIF/video their own length | how long the card shows; longer than a GIF/video loops it |
| `end_card_sound` | `music`, `own`, or a sound file | `music` | music: the song keeps going under the card and fades at the very end. own: the card video's own sound, and the song fades out. A sound file plays on top of the song. |
| `cover` | `auto`, a time like `12.5`, `none` | `auto` | saves `name_9x16_cover.jpg` and embeds it as the file's thumbnail (section 4a) |
| `safe` | `on`, `off` | `on` | vertical clips: captions stay clear of the app buttons and text (section 4a) |
| `contrast` | `on`, `off` | `on` | over a bright background, the words get a darker, wider shadow and a soft dark halo (section 4a) |
| `intro` | `gust`, `fog`, `rush`, `dark`, `none` | `none` | opening sound with a matching screen effect; the reading waits for it (section 4b) |
| `intro_sound` | path | `sfx/<intro>.*`, else synthesized | the opener's sound |
| `intro_db` | dB | per intro (about −4) | opener sound level (before loudness) |
| `intro_seconds` | seconds | per intro (1.1 to 2) | how long the screen effect lasts |
| `lead` | seconds | per intro (0.9 to 1.6) | when your reading starts; `0` puts the voice over the opener |
| `rewind_sfx` | path, `whoosh`, `wind` | none | a sound at the rewind; whoosh/wind are synthesized, played reversed so they swell into it |
| `voice_db` | dB | `0` | your reading's level against the music and effects |
| `voice_clean` | `on`, `off` | `on` | rumble cut below 80 Hz, light hiss removal, gentle compression (`VOICE_CLEAN` in render.py) |
| `loudness` | LUFS | `-14` | the finished clip's loudness; −14 is what TikTok, Reels and YouTube play at. `0` leaves the mix as is |

### 4a. Cover, safe area and contrast

**Cover.** The cover is the still people see before the clip plays: on your profile grid, in search, and as
the YouTube thumbnail. With `auto`, render.py scores frames from the clip's footage, in the clip's framing,
and keeps the most dramatic: vivid color, warm orange, red and pink light (peak sunset, golden-hour sunrise,
lit-up clouds) and strong contrast; dull grey or dark frames score low. It saves the frame without captions
and prints its time in the clip. Instagram and YouTube let you upload the jpg as the cover. TikTok has you
pick a moment from the video, so use the printed time. If you don't like the pick, set `cover` to a time
(seconds into the clip) and render again. The scoring is
`drama()` in render.py.

**Safe area.** On TikTok, Reels and Shorts the right edge has the like/comment/share buttons, the bottom
has your caption and the sound name, and the top has the app bar. In vertical clips the flat captions stay
inside the area those leave clear (`SAFE_9x16` in render.py: 64 px from the left, 150 from the right, 260
from the top, 480 from the bottom, out of 1080x1920). Caption spots on surfaces ({rooftop}, {sky}...) are part
of the scene and aren't moved.

**Contrast.** Every frame, render.py measures how bright the picture is right behind the words. On a dark
sky nothing changes. As it gets brighter (fog, white cloud, the sun low in the frame), the shadow gets
darker and wider and a soft dark halo builds behind the words, easing over about half a second so it never
flickers. The words keep their style's colors. Tune it in `brightness()` (when it starts: 0.35) and
`draw_words()` (how strong).

### 4b. Sound: the opener, your voice and the mix

**Openers.** `intro` puts a sound at the very start of the clip with a screen effect made for it. The
picture starts hidden and clears as the sound passes: the clearing follows the sound itself (how much of it
has played), so a different sound file stays in sync without retiming anything. Your reading starts at
`lead` seconds, as the gust settles; captions and the footage span shift with it.

| Intro | Sound | Screen |
|---|---|---|
| `gust` | a wind gust sweeping left to right | the picture streaks sideways (motion blur), glides in from the left and settles |
| `fog` | soft, breathy wind | the picture emerges from a pale haze, blur dissolving |
| `rush` | a fast whoosh | a zoom blur rushes in from the centre and pulls back to the scene |
| `dark` | a low swell of air | fades up from black, soft at first |

The built-in sounds are synthesized placeholders (filtered noise). To use a real one, save it in
`captions/sfx/` named after the intro (`sfx/gust.wav`, `sfx/fog.m4a`...) and every clip with that intro
picks it up, or point one clip at a file with `intro_sound`. Trim silence off the front of the file: the
effect starts at the first frame. `python captions/intro.py some_folder` writes the placeholders as wav files
so you can hear them. Add a new opener as a row in `INTROS` in intro.py (sound + look + timings).

**Your recordings.** Put them in `captions/recordings/`, named like the passage (`fog_gathers.m4a`). A
phone voice memo is fine: by default the voice is cleaned up (rumble cut, light hiss removal, gentle
compression so quiet lines aren't lost under the music). Record in a quiet room, phone about a hand's width
from your mouth and a little to the side, and leave a second of silence before you start. `voice_clean off`
skips the cleanup if you record with a good mic.

**The mix.** Opener at t=0, voice from `lead`, music at `music_db` underneath from t=0, rising by
`end_boost_db` after the reading, then the whole clip is set to −14 LUFS with one fixed gain (peaks kept
under −1 dB), so every clip plays at the same level. To change only levels, run render.py with `--remix`
(the opener's screen effect is part of the video, so changing `intro` needs a full render).

## 5. Caption styles

| Style | Words | Stress | Effects it uses |
|---|---|---|---|
| `pop` | bold white, pop in; spoken word turns orange | gold serif italic | stack, stairs |
| `serif` | bookish serif, fade in | amber italic | cascade, spread |
| `type` | typewriter, letters type out | bold red | stairs, cascade |
| `whisper` | light lowercase, rise in | heavy pale blue | cascade, spread |
| `poster` | tall condensed capitals, slide up | coral lowercase italic | stack, stairs |

## 6. Pairing passages with footage: match.py

```
.venv/bin/python captions/match.py poetry/captions.csv --out captions/matched_clips.csv
```

For each passage it writes `captions/passages/p###.txt` (never overwriting one you've edited) and a row
in the sheet:

1. **Length.** Reading time from the word count (`--wpm 140`). Over 75 s (`--long-seconds`) becomes
   16:9 long form with every frame played once, so the span grows to fit the reading, crossing days if
   needed (`--long-speed 2` covers twice the real time).
2. **Mood.** Words in the passage vote for a clip type: evening, endings, rest go to sunset;
   morning, beginnings, hope go to burn-off / dawn to midday; night, stars, silence go to night; work,
   struggle, weather go to midday; and for long readings, life, years, time go to a long stretch. The `why`
   column shows the deciding words.
3. **Any footage.** About 30% of passages (`--any 0.3`) skip the mood and take any kind of footage, since
   the weather doesn't have to match. `--any 0` always matches, `--any 1` never does. `--seed` reshuffles.
4. **Day.** The best ranked day of that kind from your pipeline review: days you kept first, never ones
   you rejected, each used once before any repeats.

Then render with `batch.py captions/matched_clips.csv`. Rows render as their recordings appear
(`captions/recordings/p042.m4a` and so on).

## 7. More dates

New footage has to go through the pipeline first: analyse the folders, plan and rank clips, review
samples (keep/reject). The pipeline thread has those commands (`pipeline.analyze`, `pipeline.clips plan`,
`pipeline.review`). Once days are in `pipeline.db`, any date-time you put in `from` / `to` works, and
match.py picks from all ranked days.

## 8. Where to tweak

| What | Where |
|---|---|
| Fonts, colors, sizes, entrance animation, stress look, which effects a style uses, effect rate | `styles.py`: `BASE` and `STYLES`. Drop a font file in `captions/fonts/` named after a role (`serif.ttf`, `heavy.otf`...) to replace it. |
| Caption height on screen | `styles.py` `y` (0.66 = just below the horizon in 9:16) |
| Long-form caption size and position | `render.py`, `main()`: the 16x9 block (`size * 0.7`, `y=0.8`, `max_words=9`) |
| Long-form framing (higher, right) | `render.py`: `LONG_FOCUS_Y` (0.47, height), `LONG_SHIFT_X` (0.3, right), `LONG_MIN_ZOOM` (1.3), plus the quarter-height raise in `crop_box()`. Every move except tilt holds this height. |
| Safe area for vertical captions | `render.py` `SAFE_9x16` |
| Contrast (when the shadow strengthens, how much) | `render.py` `brightness()` and `draw_words()` |
| Cover scoring | `render.py` `drama()` |
| Camera moves | `render.py` `MOVES`: zoom range and focus point per move (fractions of the upright frame) |
| Caption spots | `render.py` `PLANES`: four corners (top-left, top-right, bottom-right, bottom-left) in upright 1520x2688 frame pixels. Add a new name to use it as `{name}`. |
| How words group into captions | `render.py` `group()`: breaks at punctuation, pauses over 0.45 s, `max_words`; never after "the", "a", "of" |
| Which lines get effects and how often | `render.py` `fx_ranges()` (scoring) and `fx_every`; shapes in `fx_layout()` |
| Timing of words appearing and leaving | `styles.py` `pop`, `hold`, `fade` |
| Ending | render.py flags `--pause` (0.1 s), `--rewind-seconds` (2), `--hold-seconds` |
| Mood words and clip types | `match.py` `MOODS`, `RECIPE_FOR`, `ANCHOR` |
| Reading pace / long-form threshold | `match.py --wpm`, `--long-seconds` |
| Opener presets (sound, look, length, when the reading starts, level) | `intro.py` `INTROS`; looks are `streak`, `haze`, `zoom`, `fade`; placeholder sounds in `synth()` |
| Voice cleanup | `render.py` `VOICE_CLEAN` (ffmpeg filters) |
| How hard renders push the Mac | `batch.py` / `render.py --threads` (default 2, at low priority with background QoS; 0 = full speed). Rows render one at a time. |

## 9. Things that could add to this

Built: the end card (`end_card`), safe area, cover and contrast are in section 4; openers, voice cleanup
and loudness in section 4b. On hold: variations.
Not built yet:

- **Auto attribution:** "— Author, Title" written over the rewind from captions.csv, no card needed.
- **Subtitle file (.srt):** written next to each clip from the word timings, for YouTube and accessibility.
- **Room tone:** a quiet city / window ambience bed under the voice.
- **Chapters for long form:** YouTube chapter markers at stanza or section breaks.
- **Variations (on hold):** render one passage two or three ways (style, day, music) to compare.
