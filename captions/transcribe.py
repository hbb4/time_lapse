"""Word-level transcription of any video or audio file -> words.json (same shape align.py writes).

  .venv/bin/python captions/transcribe.py clip.mp4 words.json
  .venv/bin/python captions/transcribe.py clip.mp4 words.json --text passages/p042.txt   # snap to your exact text
  .venv/bin/python captions/transcribe.py clip.mp4 words.json --srt clip.srt             # also write subtitles

Two backends, both optional installs (see requirements.txt):
  faster-whisper (default; CPU/CUDA)   pip install faster-whisper
  mlx-whisper    (Apple silicon, fast) pip install mlx-whisper      use --backend mlx

With --text, the passage's own spelling, punctuation and markup replace what the model heard, and only the
timings come from the audio. Words the model missed get timings interpolated between their neighbours.
"""
import argparse, difflib, json, re, sys

import markup

MODELS = {"faster": "large-v3", "mlx": "mlx-community/whisper-large-v3-turbo"}

def norm(t):
    return re.sub(r"[^a-z0-9']", "", t.lower().replace("’", "'"))

# ---------- backends: each returns [{"word", "start", "end", "prob"}] ----------
def _faster(path, model, language, prompt, device):
    from faster_whisper import WhisperModel
    m = WhisperModel(model, device=device, compute_type="auto")
    segs, info = m.transcribe(path, word_timestamps=True, vad_filter=True, language=language,
                              initial_prompt=prompt, condition_on_previous_text=False)
    return [dict(word=w.word, start=w.start, end=w.end, prob=w.probability) for s in segs for w in s.words]

def _mlx(path, model, language, prompt, device):
    import mlx_whisper
    r = mlx_whisper.transcribe(path, path_or_hf_repo=model, word_timestamps=True, language=language,
                               initial_prompt=prompt, condition_on_previous_text=False)
    return [dict(word=w["word"], start=w["start"], end=w["end"], prob=w.get("probability", 1.0))
            for s in r["segments"] for w in s.get("words", [])]

BACKENDS = {"faster": _faster, "mlx": _mlx}

def clean(words, duration=None):
    """Tidy raw model words: trimmed, in order, never overlapping, and no word held across a silence.
    Whisper often stretches a word's end over the pause that follows it; captions would then linger."""
    out = []
    for w in words:
        text = w["word"].strip()
        if not text:
            continue
        out.append(dict(word=text, start=float(w["start"]), end=float(w["end"]), prob=round(float(w.get("prob", 1)), 3)))
    for i, w in enumerate(out):
        if i and w["start"] < out[i - 1]["end"]:
            mid = max(out[i - 1]["start"] + 0.02, min(w["start"], out[i - 1]["end"]))
            out[i - 1]["end"] = mid; w["start"] = max(w["start"], mid)
        cap = 0.18 + 0.075 * len(norm(w["word"]) or "a")           # a generous spoken length for this many letters
        w["end"] = round(min(max(w["end"], w["start"] + 0.04), w["start"] + cap), 3)
        w["start"] = round(w["start"], 3)
        if duration: w["end"] = min(w["end"], duration)
    return out

def transcribe(path, backend="faster", model=None, language=None, prompt=None, device="auto"):
    try:
        raw = BACKENDS[backend](path, model or MODELS[backend], language, prompt, device)
    except ImportError as e:
        pkg = {"faster": "faster-whisper", "mlx": "mlx-whisper"}[backend]
        raise SystemExit(f"{pkg} is not installed in this environment (pip install {pkg}): {e}")
    return clean(raw)

# ---------- snap the heard words onto known text ----------
def snap_to_text(words, text):
    """Passage tokens (markup kept) with timings borrowed from the heard words."""
    toks = markup.parse(text)
    heard = [norm(w["word"]) for w in words]
    want = [norm(t["word"]) for t in toks]
    out = [None] * len(toks)
    sm = difflib.SequenceMatcher(a=want, b=heard, autojunk=False)
    for tag, i0, i1, j0, j1 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i1 - i0):
                out[i0 + k] = (words[j0 + k]["start"], words[j0 + k]["end"])
        elif tag == "replace":                                       # same stretch of audio, different words
            a, b = words[j0]["start"], words[j1 - 1]["end"]
            step = (b - a) / (i1 - i0)
            for k in range(i1 - i0):
                out[i0 + k] = (a + k * step, a + (k + 1) * step)
    # tokens nothing matched (insert) or with no letters: spread between the nearest timed neighbours
    i = 0
    while i < len(out):
        if out[i] is not None:
            i += 1; continue
        j = i
        while j < len(out) and out[j] is None: j += 1
        a = out[i - 1][1] if i else (words[0]["start"] if words else 0.0)
        b = out[j][0] if j < len(out) else (words[-1]["end"] if words else a)
        b = max(b, a)
        step = (b - a) / (j - i)
        for k in range(i, j):
            out[k] = (a + (k - i) * step, a + (k - i + 1) * step)
        i = j
    res = [dict(word=t["word"], start=round(s, 3), end=round(e, 3)) for t, (s, e) in zip(toks, out)]
    return markup.apply(res, text)

def match_ratio(words, text):
    """0-1: how much of the passage the audio actually says (a sanity check before trusting a snap)."""
    a = [norm(t["word"]) for t in markup.parse(text)]
    b = [norm(w["word"]) for w in words]
    return difflib.SequenceMatcher(a=a, b=b, autojunk=False).ratio() if a and b else 0.0

# ---------- plain speech -> the fields the renderer expects ----------
def as_plain(words):
    """Add the markup fields render.group() reads to words that came from speech rather than a marked-up passage."""
    out = []
    for i, w in enumerate(words):
        out.append(dict(w, emph=w.get("emph", False), plane=None, fx=None, brk=False, line=0,
                        line_end=(i == len(words) - 1), stanza_end=(i == len(words) - 1)))
    return out

# ---------- subtitles ----------
def cues(words, max_chars=42, max_dur=5.0, gap=0.7):
    """Group words into subtitle cues at punctuation, pauses and line length."""
    out, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        text = " ".join(x["word"] for x in cur)
        if (not nxt or w["word"][-1] in ".!?" or nxt["start"] - w["end"] > gap
                or len(text) + len(nxt["word"]) + 1 > max_chars or nxt["end"] - cur[0]["start"] > max_dur):
            out.append((cur[0]["start"], w["end"], text)); cur = []
    return out

def _stamp(t, comma):
    ms = int(round(t * 1000)); h, ms = divmod(ms, 3600000); m, ms = divmod(ms, 60000); s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{',' if comma else '.'}{ms:03d}"

def to_srt(words, **kw):
    return "".join(f"{i}\n{_stamp(a, True)} --> {_stamp(b, True)}\n{t}\n\n" for i, (a, b, t) in enumerate(cues(words, **kw), 1))

def to_vtt(words, **kw):
    return "WEBVTT\n\n" + "".join(f"{_stamp(a, False)} --> {_stamp(b, False)}\n{t}\n\n" for a, b, t in cues(words, **kw))

def speech_stats(words):
    """Words per minute over the spoken stretch, and the share of the clip that is silence between words."""
    if len(words) < 2: return dict(wpm=0.0, pause_share=0.0)
    span = words[-1]["end"] - words[0]["start"]
    pauses = sum(max(0, b["start"] - a["end"]) for a, b in zip(words, words[1:]))
    return dict(wpm=round(len(words) / span * 60, 1) if span > 0 else 0.0, pause_share=round(pauses / span, 3) if span > 0 else 0.0)

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("media"); ap.add_argument("out")
    ap.add_argument("--text", help="the passage (markup allowed): its words replace the heard ones, timings stay")
    ap.add_argument("--backend", choices=sorted(BACKENDS), default="faster")
    ap.add_argument("--model"); ap.add_argument("--language")
    ap.add_argument("--srt"); ap.add_argument("--vtt")
    a = ap.parse_args()
    text = open(a.text, encoding="utf-8").read() if a.text else None
    words = transcribe(a.media, a.backend, a.model, a.language, prompt=markup.plain(text) if text else None)
    if text:
        r = match_ratio(words, text)
        print(f"audio matches the passage {r:.0%}", file=sys.stderr)
        if r < 0.6: print("  low: is this the right recording for that text?", file=sys.stderr)
        words = snap_to_text(words, text)
    json.dump(words, open(a.out, "w"), indent=1)
    if a.srt: open(a.srt, "w", encoding="utf-8").write(to_srt(words))
    if a.vtt: open(a.vtt, "w", encoding="utf-8").write(to_vtt(words))
    for w in words: print(f"{w['start']:6.2f} {w['end']:6.2f}  {w['word']}")

if __name__ == "__main__":
    main()
