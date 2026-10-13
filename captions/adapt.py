"""Make caption choices from the content instead of by hand: which words to stress, which style to use.

  emphasize(words)            mark a few punchy words as stressed (shown alone, big) with no markup needed
  pick_style(words, ...)      style name + the reasons, from speech pace, tone, footage brightness and shape

Both are deterministic for a given clip name, so a re-render keeps its look. Anything you mark yourself in a
passage (*stress*, @style ...) is left alone: these only fill in what is unmarked.
"""
import re, zlib

import transcribe

# words that are not worth stressing even when long
COMMON = set("""about after again against almost already also always another around because before being between both
but could didn't does doing don't down during each either enough even ever every first from further getting going
great have having here how however into just know like little made make many might more most much must never next
none only other our out over really right same should since some something still such take than that their them then
there these they thing things think this those though through today together too under until very want were what when
where which while whole will with without would your yours you're it's that's there's we're""".split())

HYPE = set("""never always secret nobody everything nothing everyone impossible insane crazy huge massive free
stop wait listen watch remember imagine love hate fear truth lie real fake best worst only first last
now today tonight tomorrow forever suddenly finally literally actually exactly""".split())

CALM = set("""quiet soft slowly gently still breath evening fog dusk dawn silence rest wind light shadow
memory afternoon morning sleep dream moon stars river rain mist hush""".split())

def _is_number(t):
    return bool(re.search(r"\d", t)) or t.lower().strip(".,!?") in {"million", "billion", "thousand", "percent", "hundred"}

def emphasize(words, every=8.0, first_after=1.2, max_len=24):
    """Flag up to one stressed word per `every` seconds. Candidates are numbers, ALL CAPS, words from a punchy
    list, the word after a dramatic pause, and long uncommon words; each is scored and the best are kept, spread
    out. Returns a copy; words already stressed in a marked-up passage are kept and block their neighbourhood."""
    out = [dict(w) for w in words]
    if len(out) < 8: return out
    chosen = [w["start"] for w in out if w.get("emph")]
    cands = []
    for i, w in enumerate(out):
        if w.get("emph") or w.get("plane") or w.get("fx") or w["start"] < first_after:
            continue
        bare = re.sub(r"[^\w'%$]", "", w["word"])
        low = bare.lower()
        if not bare or len(bare) > max_len or low in COMMON:
            continue
        score = 0.0
        if _is_number(bare): score += 3
        if len(bare) >= 2 and bare.isupper() and bare.isalpha() and bare not in ("I", "A"): score += 3
        if low in HYPE: score += 2.5
        prev = out[i - 1] if i else None
        if prev and w["start"] - prev["end"] >= 0.35: score += 1.5                # arrives after a beat of silence
        if len(low) >= 8: score += 1
        if w["word"][-1] in "!?": score += 1
        if i + 1 < len(out) and out[i + 1]["start"] - w["end"] >= 0.35: score += 0.5   # lands before a pause
        if score >= 2.5:
            cands.append((score, i))
    for score, i in sorted(cands, key=lambda c: (-c[0], c[1])):
        t = out[i]["start"]
        if all(abs(t - c) >= every for c in chosen):
            out[i]["emph"] = True; chosen.append(t)
    return out

def tone(words):
    text = " ".join(w["word"].lower() for w in words)
    toks = re.findall(r"[a-z']+", text)
    calm = sum(t in CALM for t in toks); hype = sum(t in HYPE for t in toks)
    bang = text.count("!") / max(1, len(toks)) * 20
    return calm, hype + bang

def pick_style(words, name="", aspect="9x16", brightness=None):
    """Return (style, [reasons]). brightness is 0-1 mean luma of the footage (None = unknown)."""
    st = transcribe.speech_stats(words)
    calm, hype = tone(words)
    why, pool = [], None
    if aspect == "16x9":
        pool = ["clean", "serif"] if st["wpm"] < 140 else ["clean", "pop"]
        why.append("long form: calm, smaller captions")
    elif st["wpm"] >= 165 or hype >= 2:
        pool = ["hype", "pop"]
        why.append(f"fast/energetic ({st['wpm']:.0f} wpm, punch score {hype:.1f})")
    elif st["wpm"] and st["wpm"] < 120 or calm >= 2:
        pool = ["serif", "whisper"] if (brightness is None or brightness < 0.55) else ["serif", "clean"]
        why.append(f"slow/reflective ({st['wpm']:.0f} wpm, calm score {calm})")
    else:
        pool = ["pop", "clean", "poster"]
        why.append(f"conversational ({st['wpm']:.0f} wpm)")
    if brightness is not None and brightness > 0.62:
        pool = [p for p in pool if p in ("hype", "pop", "poster", "clean")] or ["hype"]
        why.append(f"bright footage ({brightness:.2f}): styles with strong outlines/shadows")
    choice = pool[zlib.crc32(name.encode()) % len(pool)]
    return choice, why
