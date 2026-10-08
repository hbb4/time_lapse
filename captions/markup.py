"""Passage markup, shared by align.py and render.py.

  *gathers*        stress: shown alone on screen while spoken, in the style's stress font and color
                   (can wrap several words: *red light.*)
  {edge}slowly,    this word starts a phrase placed on a named surface in the scene (see PLANES in render.py):
                   edge, front, side, rooftop, yellow, tan, hills, sky, clouds
  |                force a phrase break here
  [stairs]and then  this word starts a line drawn with a layout effect, running to the end of its line
                   (or sentence, up to 12 words): stairs, cascade, stack, spread. [plain] turns the
                   automatic effect off for that line.
  @style serif      (lines at the top) clip settings, same names as the clips.csv columns
Everything else is the plain passage text, punctuation kept. Line and stanza breaks are remembered
(render.py gives the last line of each stanza and the closing line an effect automatically).
"""
import re

def directives(text):
    """Clip settings written at the top of a passage file as "@name value" lines, e.g. "@style serif"."""
    out = {}
    for line in text.split("\n"):
        m = re.match(r"^\s*@(\w+)\s+(.*?)\s*$", line)
        if m: out[m.group(1).lower()] = m.group(2)
    return out

def body(text):
    """The passage without its @ lines (and without the blank lines right after them)."""
    lines = [l for l in text.split("\n") if not re.match(r"^\s*@\w+", l)]
    while lines and not lines[0].strip(): lines.pop(0)
    return "\n".join(lines)

def parse(text):
    out, emph, brk = [], False, False
    lines = body(text).split("\n")
    for li, line in enumerate(lines):
        toks = line.split()
        stanza_end = li + 1 >= len(lines) or not lines[li + 1].strip()
        for ti, tok in enumerate(toks):
            if tok == "|":
                brk = True; continue
            plane = fx = None
            while True:
                m = re.match(r"^\{(\w+)\}(.*)$", tok) or re.match(r"^\[(\w+)\](.*)$", tok)
                if not m: break
                if tok[0] == "{": plane = m.group(1)
                else: fx = m.group(1)
                tok = m.group(2)
            start = tok.startswith("*")
            tok = tok.lstrip("*")
            end = bool(re.search(r"\*[^\w]*$", tok))
            tok = tok.replace("*", "")
            if start: emph = True
            if tok:
                out.append(dict(word=tok, emph=emph, plane=plane, fx=fx, brk=brk or plane is not None or fx is not None,
                                line=li, line_end=False, stanza_end=False))
                brk = False
            if end: emph = False
        if out and out[-1]["line"] == li:
            out[-1]["line_end"] = True
            out[-1]["stanza_end"] = stanza_end
    return out

def plain(text):
    return " ".join(t["word"] for t in parse(text))

def apply(words, text):
    """Copy markup flags onto aligned words (same order and count as the passage tokens)."""
    toks = parse(text)
    if len(toks) != len(words):
        raise SystemExit(f"passage has {len(toks)} words but the alignment has {len(words)}")
    for w, t in zip(words, toks):
        w.update({k: t[k] for k in ("emph", "plane", "fx", "brk", "word", "line", "line_end", "stanza_end")})
    return words
