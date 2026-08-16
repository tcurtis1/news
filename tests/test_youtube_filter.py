import unittest
from app.trends import is_music_video


class YouTubeFilterTests(unittest.TestCase):
    def test_music_video_titles_filtered(self):
        music_samples = [
            "Taylor Swift - Fortnight (Official Music Video) ft. Post Malone",
            "Sabrina Carpenter - Taste (Official Video)",
            "Billie Eilish - BIRDS OF A FEATHER (Official Lyric Video)",
            "Post Malone - I Had Some Help (feat. Morgan Wallen)",
            "KATSEYE - Touch (Official MV)",
            "Kendrick Lamar - Not Like Us (Audio)",
            "Chappell Roan - Good Luck, Babe! (Official Audio Visualizer)",
            "Eminem - Houdini [Official Music Video]",
            "Coldplay - feelslikeimfallinginlove (Visualizer)",
            "Ariana Grande - we can't be friends (wait for your love) [Live Performance]",
            "Linkin Park - The Emptiness Machine (Official Track)",
            "Zack Bia - Full Album Stream",
        ]
        for title in music_samples:
            self.assertTrue(
                is_music_video(title),
                f"Expected '{title}' to be identified as a music video",
            )

    def test_artists_metadata_filtered(self):
        self.assertTrue(
            is_music_video("Sunflower", artists=[{"name": "Post Malone"}]),
            "Metadata with artists should be identified as music",
        )

    def test_non_music_news_sports_viral_allowed(self):
        allowed_samples = [
            ("PGA TOUR Highlights | Round 3 | FedEx St. Jude", "PGA TOUR"),
            ("Iran urges U.S. to 'accept the reality of defeat'", "NBC News"),
            ("NASCAR Cup Series Highlights | 2026 Richmond Raceway", "NASCAR"),
            ("Minnesota Vikings vs. New York Giants | 2026 Preseason Week 1", "NFL"),
            ("Novak Djokovic vs Thiago Tirante Highlights | Cincinnati 2026", "Tennis TV"),
            ("Joshua Báez Hits THREE Home Runs in His MLB Debut", "MLB"),
            ("SpaceX Starship Orbital Test Flight 5 Highlights", "SpaceX"),
            ("Apple Event 2026: Everything Announced in 10 Minutes", "The Verge"),
            ("Why Inflation Is Cooling Faster Than Expected", "Wall Street Journal"),
            ("State of the Union Analysis with Special Report", "PBS NewsHour"),
        ]
        for title, channel in allowed_samples:
            self.assertFalse(
                is_music_video(title, channel=channel),
                f"Expected news/sports title '{title}' to be allowed",
            )


if __name__ == "__main__":
    unittest.main()
