# publish

Caption a local video and post it to YouTube (Shorts and long form), TikTok, Instagram Reels, Facebook and X.

```
python -m publish ship ~/Movies/clip.mp4
```

```
clip.mp4 ──► captions/burn.py ──► one upload-ready file ──► route ──► write the copy ──► queue ──► post
             Whisper word timings    H.264/AAC, faststart    which     title, caption,    SQLite   one adapter
             style picked from the   (remuxed if it's        platforms hashtags per       ledger   per platform,
             content, stressed words already fine)           fit it    platform's limits           retries, no doubles
             jump-cut zooms, music
             ducking, -14 LUFS
```

Everything runs from the repo root. Nothing is posted by `plan` or `ship --dry-run`.

## Commands

| | |
|---|---|
| `ship VIDEO` | caption + edit, export, write the copy, post to every platform that fits |
| `ship VIDEO --dry-run` | the same, minus posting: look at `~/.local/share/time_lapse/publish/work/<id>/*.captioned.mp4` first |
| `plan VIDEO` | what would go where and with what words (no rendering) |
| `run [--every 300]` | post what is queued and due, retry failures; for cron / launchd |
| `watch FOLDER` | ship every video dropped in FOLDER (moved to `_posted/` afterwards), and keep running the queue |
| `status` | recent posts, links and errors |
| `auth youtube\|tiktok\|x\|meta` | log in once per platform (`meta` covers Instagram and Facebook) |
| `doctor` | what is installed and which platforms still need credentials |

Useful flags: `--platforms youtube,x`, `--privacy private`, `--at "2026-10-14 18:00"`, `--title`, `--short "text for X/TikTok"`,
`--hashtags a,b`, `--text passage.txt`, `--style hype`, `--edit punchy`, `--cut-silence`, `--music bed.mp3`,
`--captions done` (the video already has captions), `--force` (post again).

A video is never posted twice to the same platform (the ledger keys on the file's SHA-256) unless you pass `--force`.
A failure on one platform never blocks the others; `run` retries it (3 attempts by default, then `run --requeue`).

### Per-video overrides: `clip.post.toml` next to the video

```toml
title = "Fog over Telegraph Hill"
description = "Shot from my window."
hashtags = ["sanfrancisco"]
link = "https://example.com"
publish_at = "2026-10-14 18:00"
platforms = ["youtube", "instagram"]
text_file = "captions/passages/fog_gathers.txt"   # the passage, if there is one
[x]
text = "Fog, again."
```

Command-line flags beat the sidecar, which beats settings.toml, which beats the defaults.

## Captions (captions/)

`captions/burn.py` is the captioner; `publish` calls it, and you can run it alone (`captions/burn.py in.mp4 out.mp4 --help`).

- **Words.** Whisper (`faster-whisper`, or `mlx-whisper` on Apple silicon: `--backend mlx`) gives word timings. If you pass the
  passage (`--text`), its exact spelling, punctuation and markup replace what the model heard and only the timings come from audio
  (`captions/transcribe.py`, which also warns if the recording doesn't match the text).
- **Adaptive style.** `--style auto` picks from speech pace, tone and footage brightness (`captions/adapt.py`): fast or punchy speech
  gets `hype`/`pop`, slow or reflective gets `serif`/`whisper`, long form gets `clean`. Two new styles (`hype`: outlined caps with a
  swelling highlight; `clean`) join the five existing ones. Re-rendering a clip keeps its look.
- **Stressed words.** A few punchy words (numbers, ALL CAPS, "never", words after a beat of silence) are shown alone and big, at most one
  per 8 s. Words you mark yourself in a passage (`*stress*`) are respected.
- **Trendy edits.** `--edit punchy`: the framing alternates 1.0x / 1.09x at each sentence (jump-cut zoom), pushes in slowly inside each
  shot and punches on stressed words; `--cut-silence` removes dead air; `--music` is ducked under the voice; audio is normalised to
  -14 LUFS; `--progress` adds a progress bar. Captions respect the TikTok/Reels/Shorts safe area and get a contrast shadow on bright footage.
- **Extras.** A cover image (the frame at the first stressed word) and an `.srt`, which is uploaded to YouTube long-form videos.

Your existing time-lapse clips from `captions/render.py` / `batch.py` are already captioned: ship them with
`--captions done --text captions/passages/<name>.txt`.

## Setting up each platform

Credentials go in `~/.config/time_lapse/publish/.env` (see `.env.example`); tokens are stored in `tokens.json` next to it (mode 0600).
Each platform has a gate you can't skip, so start these early:

| | Setup | Gate to know about |
|---|---|---|
| **YouTube** | Google Cloud project, enable *YouTube Data API v3*, OAuth client (Desktop). `auth youtube`. | Until Google audits your API project, uploads are forced **private**. Set the consent screen to *In production* (not *Testing*) or the login expires every 7 days. One upload costs 1,600 of 10,000 daily quota units, so about 6 a day. |
| **TikTok** | developers.tiktok.com app with *Content Posting API* and a redirect URI. `auth tiktok`. | Until TikTok audits the app, direct posts are forced to *only me*. `tiktok_mode = "draft"` sends videos to your inbox instead (you tap Post). |
| **Instagram** | Instagram professional account linked to a Facebook Page, Meta Business app. `auth meta`. | Works on your own accounts in the app's development mode; no app review is needed for that. |
| **Facebook** | Same Meta app and `auth meta` (one login covers both). | Same as Instagram. |
| **X** | developer.x.com app with OAuth 2.0 user auth. `auth x`. | Posting through the API needs a paid X API plan. |

Instagram, Facebook and X have no private mode, so `--privacy private|unlisted` posts only to YouTube and TikTok.

## What is and isn't verified

The adapters follow each platform's public API docs and are tested against scripted responses (`python -m unittest discover -s tests`),
including the exact request sequence and payloads, retries, token refresh and failure isolation. **They have not been run against the live
APIs**, which need your credentials. Platform APIs and limits change; limits are in `settings.toml` rather than code for that reason. For the first
real run, use `ship clip.mp4 --privacy private` (YouTube and TikTok only), then a throwaway Page/Instagram account or a short post you can delete
for the platforms with no private mode.

## Running it unattended on a Mac

`python -m publish watch ~/ToPost` in a terminal, or a launchd job that runs `python -m publish run` every few minutes (queued, scheduled and
retryable posts), with the repo's `.venv` python and the working directory set to the repo.

## Install

```
.venv/bin/pip install -r requirements.txt        # requests, faster-whisper, ...
brew install ffmpeg
.venv/bin/python -m publish doctor
```
