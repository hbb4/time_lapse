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

Everything runs from the repo root. Nothing is posted by `plan` or `ship --dry-run`. **By default nothing is posted until you
approve it** (see Review below).

## Commands

| | |
|---|---|
| `ship VIDEO` | caption + edit, export, write the copy, then wait for your approval (or post, per the review mode) |
| `review` | posts waiting for approval, with the path of the rendered video to watch |
| `approve JOB` | release a waiting post (`--platforms x,tiktok` for just some, `--now` to skip a schedule or review delay) |
| `reject JOB` | cancel a waiting or scheduled post |
| `edit JOB PLATFORM --text "..."` | change the words of one post before it goes out |
| `accounts` | every account in settings.toml and what each still needs |
| `notify-test` | send a test message to an account's notification channels |
| `ship VIDEO --dry-run` | the same, minus posting: look at `~/.local/share/time_lapse/publish/work/<id>/*.captioned.mp4` first |
| `plan VIDEO` | what would go where and with what words (no rendering) |
| `run [--every 300]` | collect Telegram taps, post what is queued and due, retry failures; for cron / launchd |
| `watch [FOLDER]` | ship every video dropped in FOLDER, or in each account's own `watch` folder (moved to `_posted/` afterwards), and keep running the queue |
| `status` | recent posts, links and errors |
| `auth youtube\|tiktok\|x\|meta` | log in once per platform (`meta` covers Instagram and Facebook) |
| `doctor` | what is installed and which platforms still need credentials |

Useful flags: `--platforms youtube,x`, `--privacy private`, `--at "2026-10-14 18:00"`, `--title`, `--short "text for X/TikTok"`,
`--hashtags a,b`, `--text passage.txt`, `--style hype`, `--edit punchy`, `--cut-silence`, `--music bed.mp3`,
`--captions done` (the video already has captions), `--force` (post again).

A video is never posted twice to the same platform (the ledger keys on the file's SHA-256) unless you pass `--force`.
A failure on one platform never blocks the others; `run` retries it (3 attempts by default, then `run --requeue`).

## Review: the layer between rendered and posted

Set per account (`[review] mode`, or `--review` on one run):

| mode | what happens |
|---|---|
| `manual` (default) | the rendered video goes to your phone/desktop with the planned copy; nothing posts until you approve |
| `delay` | same notification, but it posts itself after `delay_minutes` unless you reject it; approving skips the wait |
| `auto` | posts at once; you are still told when it is up (or fails) |

Moving an account from `manual` to `delay` to `auto` is a one-line change in settings.toml, so you can loosen it as you trust the output.
Notifications, per account (`[notify] channels`):

- **Telegram** (two-way): the video (a small preview if it is over 50 MB) arrives with **Approve / Reject** buttons. `run` and `watch`
  collect your taps on their next pass (every 30 s in `watch`). You can also type `/approve 12`, `/reject 12` or `/queue`. Only the chat
  you configured for that account can approve its posts.
- **ntfy**, **Discord**, **macOS**: one-way. Approve at the terminal with `python -m publish approve 12`.
- You are told when a video is ready, when it is posted (with links, one message per video), and when something fails or a login has
  expired (once per distinct problem, not every retry).

## Accounts

Each account is an `[accounts.NAME]` table in settings.toml that overrides anything above it: platforms, hashtags, caption style and
music, review mode, notification channel and a `watch` folder. Logins are per account:
`python -m publish auth x --account sf_clouds` (sign in to that account in the browser first). One developer app per platform can authorize
many accounts, so the keys in `.env` are shared. A video is posted at most once per account and platform, so the same file can go to two accounts.
Use `--account NAME` on any command; `python -m publish watch` with no folder watches every account's `watch` folder.

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
| **TikTok** | developers.tiktok.com app with *Content Posting API* and a redirect URI. `auth tiktok`. | See "TikTok and the audit" below. The default `tiktok_mode = "draft"` sends each video to your TikTok inbox and you tap Post. |
| **Instagram** | Instagram professional account linked to a Facebook Page, Meta Business app. `auth meta`. | Works on your own accounts in the app's development mode; no app review is needed for that. |
| **Facebook** | Same Meta app and `auth meta` (one login covers both). | Same as Instagram. |
| **X** | developer.x.com app with OAuth 2.0 user auth. `auth x`. | The API is paid, per use: prepaid credits, billed per post, and (per the pricing pages I could find) a post containing a URL costs many times a plain one. So `x_include_link` is off by default. Check the current rates in the X Developer Console. |

Instagram, Facebook and X have no private mode, so `--privacy private|unlisted` posts only to YouTube and TikTok.

## TikTok and the audit

"Audit" is TikTok's review of your *app* (the developer registration), not of each video. Until an app passes it, per TikTok's docs:
posts made through the API are private ("only me"), at most 5 users can post through the app per 24 hours, and the TikTok account must itself
be set to private when you post. After it passes, posts can be public. TikTok's content-sharing rules also expect a person to see a preview
and explicitly confirm each post, so an unattended bot is the wrong shape for direct posting. That is why:

- `tiktok_mode = "draft"` is the default: the video is uploaded to your TikTok inbox and you tap Post in the app (the caption to paste is in
  the notification). It needs no audit as far as I can tell, but I could not reach TikTok's docs from here to confirm that, so test it first.
- A post nobody approved (`review = auto` or `delay`) is **always** a draft, even if you set `tiktok_mode = "direct"`, unless you set
  `tiktok_direct_unattended = true` (against TikTok's guidelines; don't).
- If you want direct, public posting, plan on the audit (reports say weeks) with a manual-approve flow like the one here as the thing you demo.
- The audit belongs to the app, not the account, so several TikTok accounts share one app (and one audit).

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
