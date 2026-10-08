"""Forced alignment: known passage text + audio -> per-word start/end times (words.json).

Uses PocketSphinx (offline, bundled English model; pip install pocketsphinx in the venv).
  .venv/bin/python captions/align.py recording.m4a passage.txt words.json
If it drifts on your voice, a WhisperX / torchaudio MMS aligner can replace align() with the same output.
"""
import json, re, subprocess, sys
import markup
from pocketsphinx import Decoder

def load_pcm16k(path):
    return subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", "16000",
                           "-f", "s16le", "-"], capture_output=True, check=True).stdout

def align(audio, text):
    tokens = markup.plain(text).split()                     # displayed words, punctuation kept, markup removed
    norm = [re.sub(r"[^a-z']", "", t.lower()) for t in tokens]
    d = Decoder(samprate=16000, bestpath=False, beam=1e-80, wbeam=1e-80, pbeam=1e-80, loglevel="FATAL")
    d.set_align_text(" ".join(n for n in norm if n))
    d.start_utt(); d.process_raw(load_pcm16k(audio), full_utt=True); d.end_utt()
    segs = [(s.word, s.start_frame / 100, (s.end_frame + 1) / 100) for s in d.seg()
            if s.word not in ("<s>", "</s>", "<sil>", "[NOISE]")]
    out, i = [], 0
    for tok, n in zip(tokens, norm):
        if not n:                                   # a lone dash or symbol: shown with the word before it
            t = out[-1]["end"] if out else 0.0
            out.append({"word": tok, "start": t, "end": t}); continue
        w, st, en = segs[i]; i += 1
        out.append({"word": tok, "start": round(st, 3), "end": round(en, 3)})
    return out

if __name__ == "__main__":
    words = align(sys.argv[1], open(sys.argv[2]).read())
    json.dump(words, open(sys.argv[3], "w"), indent=1)
    for w in words: print(f"{w['start']:6.2f} {w['end']:6.2f}  {w['word']}")
