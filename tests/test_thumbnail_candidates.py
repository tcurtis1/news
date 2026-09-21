import unittest

from app import search


class ThumbnailSearchCandidatesTests(unittest.TestCase):
    def test_explainer_headline_keeps_the_real_subject(self):
        # Regression: "A simple guide to the Yemen conflict" used to collapse
        # to a bare "simple guide" fallback query once "Yemen conflict" was
        # dropped by the 2-word truncation -- generic enough to match an
        # unrelated article (observed: a shopping listicle about creatine
        # supplements) and use its image with no relevance check.
        candidates = search._thumbnail_search_candidates("A simple guide to the Yemen conflict")
        self.assertEqual(candidates, ["Yemen conflict", "Yemen"])
        for q in candidates:
            self.assertNotIn("guide", q.lower())
            self.assertNotIn("simple", q.lower())

    def test_other_explainer_boilerplate_is_stripped(self):
        for title in (
            "Everything you need to know about the shutdown",
            "The complete guide to student loan forgiveness",
            "Ultimate guide to the government shutdown",
        ):
            candidates = search._thumbnail_search_candidates(title)
            joined = " ".join(candidates).lower()
            for boilerplate in ("everything", "need", "know", "complete", "guide", "ultimate"):
                self.assertNotIn(boilerplate, joined, msg=f"{boilerplate!r} leaked through for {title!r}")

    def test_ordinary_headline_still_yields_progressive_candidates(self):
        candidates = search._thumbnail_search_candidates("Friedrich Merz's political crisis threatens Germany")
        self.assertTrue(candidates)
        self.assertEqual(candidates[0], "Friedrich Merz s political")

    def test_empty_title_yields_no_candidates(self):
        self.assertEqual(search._thumbnail_search_candidates(""), [])


if __name__ == "__main__":
    unittest.main()
