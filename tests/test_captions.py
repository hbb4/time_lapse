import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "captions"))
import adapt, burn, transcribe  # noqa: E402

def W(text, start=0.0, step=0.4, dur=0.3):
    out, t = [], start
    for w in text.split():
        out.append(dict(word=w, start=round(t, 3), end=round(t + dur, 3))); t += step
    return out

class Snap(unittest.TestCase):
    def test_passage_spelling_and_markup_win(self):
        heard = W("the fog does not arrive all at once it gathers at the edges")
        text = "The fog does not arrive all at once. It *gathers* at the {hills}edges"
        out = transcribe.snap_to_text(heard, text)
        self.assertEqual([w["word"] for w in out][:3], ["The", "fog", "does"])
        gather = next(w for w in out if w["word"] == "gathers")
        self.assertTrue(gather["emph"])
        self.assertEqual(gather["start"], heard[9]["start"])

    def test_missed_and_misheard_words_get_interpolated_times(self):
        heard = W("the evening lets it go")
        out = transcribe.snap_to_text(heard, "The evening, without asking anyone, lets it go.")
        starts = [w["start"] for w in out]
        self.assertEqual(starts, sorted(starts))
        self.assertEqual(len(out), 8)
        self.assertGreaterEqual(out[2]["start"], out[1]["end"] - 1e-6)     # "without" sits in the gap after "evening"

    def test_match_ratio_flags_wrong_recording(self):
        self.assertGreater(transcribe.match_ratio(W("one two three four"), "One two three four"), 0.9)
        self.assertLess(transcribe.match_ratio(W("completely different words here"), "One two three four"), 0.3)

    def test_clean_caps_words_stretched_over_silence(self):
        out = transcribe.clean([dict(word=" Hi", start=0.0, end=3.0), dict(word="there", start=3.1, end=3.4)])
        self.assertLess(out[0]["end"], 0.6)

    def test_srt_cues(self):
        srt = transcribe.to_srt(W("Hello there. How are you doing today my friend"))
        self.assertIn("00:00:00,000 --> ", srt)
        self.assertIn("Hello there.", srt)

class Adapt(unittest.TestCase):
    def test_emphasis_is_sparse_and_picks_punchy_words(self):
        ws = transcribe.as_plain(W("so I paid 4000 dollars for this camera and it was honestly the worst decision I ever made in my "
                                   "entire life because nobody told me about the battery and then everything changed", step=0.5))
        out = adapt.emphasize(ws, every=4.0)
        picked = [w["word"] for w in out if w["emph"]]
        self.assertTrue(0 < len(picked) <= 4, picked)
        self.assertIn("4000", picked)

    def test_style_follows_pace_and_shape(self):
        fast = transcribe.as_plain(W("stop scrolling this will change everything you know " * 4, step=0.3, dur=0.25))
        slow = transcribe.as_plain(W("the fog gathers slowly at the edges of the quiet evening " * 2, step=0.7, dur=0.5))
        self.assertIn(adapt.pick_style(fast, "a")[0], ("hype", "pop"))
        self.assertIn(adapt.pick_style(slow, "a")[0], ("serif", "whisper"))
        self.assertIn(adapt.pick_style(slow, "a", aspect="16x9")[0], ("clean", "serif"))

class Edit(unittest.TestCase):
    def test_cuts_shorten_long_pauses_and_remap_words(self):
        ws = W("one two three", step=0.4) + W("four five", start=6.0, step=0.4)
        segs = burn.plan_cuts(ws, 10.0)
        self.assertEqual(len(segs), 2)
        new, total = burn.remap(ws, segs)
        self.assertLess(total, 6.0)
        self.assertLess(new[3]["start"] - new[2]["end"], 0.6)                # the pause is now short
        self.assertAlmostEqual(new[1]["end"] - new[1]["start"], ws[1]["end"] - ws[1]["start"], places=2)

    def test_zoom_alternates_per_shot_and_punches_on_stress(self):
        ws = W("First sentence here. Second sentence goes on for a while. Third one.", step=1.0, dur=0.5)
        starts = burn.shot_starts(ws, [(0.0, 20.0)])
        self.assertGreaterEqual(len(starts), 2)
        e = dict(cut=0.1, push=0.0, punch=0.0)
        self.assertAlmostEqual(burn.zoom_at(starts[0] + 0.1, starts, ws, e), 1.0)
        self.assertAlmostEqual(burn.zoom_at(starts[1] + 0.1, starts, ws, e), 1.1)
        ws[2]["emph"] = True
        p = dict(cut=0, push=0, punch=0.06)
        self.assertGreater(burn.zoom_at(ws[2]["start"] + 0.06, [0.0], ws, p), 1.05)
        self.assertAlmostEqual(burn.zoom_at(ws[2]["start"] + 2.0, [0.0], ws, p), 1.0, places=3)

if __name__ == "__main__":
    unittest.main()
