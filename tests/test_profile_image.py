import importlib.util
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

SPEC = importlib.util.spec_from_file_location("profile_image", Path(__file__).parents[1] / "scripts/profile_image.py")
profile = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(profile)
NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)


def days():
    start = NOW.date() - timedelta(days=364)
    return [{"date": (start + timedelta(days=i)).isoformat(), "count": i % 4} for i in range(365)]


def snapshot():
    calendar = days()
    return {
        "schema": 1, "login": "m7md-d", "source": "github-graphql",
        "updatedAt": NOW.isoformat(), "from": calendar[0]["date"], "to": calendar[-1]["date"],
        "days": calendar, "contributions": sum(d["count"] for d in calendar),
        "commits": 100, "issues": 2, "pullRequests": 3, "publicRepos": 2,
        "stars": 5, "forks": 1, "languages": {"C": 20, "Dart": 80},
    }


def connection(nodes, cursor=None):
    return {"nodes": nodes, "pageInfo": {"hasNextPage": bool(cursor), "endCursor": cursor}}


def languages(name, size, cursor=None):
    return {"edges": [{"size": size, "node": {"name": name}}],
            "pageInfo": {"hasNextPage": bool(cursor), "endCursor": cursor}}


class ProfileImageTests(unittest.TestCase):
    def test_all_repository_and_language_pages_are_counted(self):
        calls = []
        calendar = days()

        def fake(query, variables):
            calls.append((query, variables))
            if query == profile.ACTIVITY_QUERY:
                return {"user": {"contributionsCollection": {
                    "totalCommitContributions": 100, "totalIssueContributions": 2,
                    "totalPullRequestContributions": 3,
                    "contributionCalendar": {"weeks": [{"contributionDays": [
                        {"date": d["date"], "contributionCount": d["count"]} for d in calendar
                    ]}]},
                }}}
            if query == profile.LANGUAGES_QUERY:
                self.assertEqual(variables["cursor"], "langs-next")
                return {"repository": {"languages": languages("Python", 10)}}
            if variables["cursor"] is None:
                return {"user": {"repositories": connection([
                    {"name": "one", "stargazerCount": 2, "forkCount": 1,
                     "languages": languages("C", 20, "langs-next")},
                ], "repos-next")}}
            self.assertEqual(variables["cursor"], "repos-next")
            return {"user": {"repositories": connection([
                {"name": "two", "stargazerCount": 3, "forkCount": 0,
                 "languages": languages("C", 30)},
            ])}}

        result = profile.fetch_snapshot("test", NOW, fake)
        self.assertEqual(result["languages"], {"C": 50, "Python": 10})
        self.assertEqual((result["publicRepos"], result["stars"], result["forks"]), (2, 5, 1))
        self.assertEqual(len(calls), 4)
        self.assertIn("privacy: PUBLIC", profile.REPOS_QUERY)
        self.assertIn("isFork: false", profile.REPOS_QUERY)

    def test_non_advancing_pagination_is_rejected(self):
        with self.assertRaises(RuntimeError):
            profile.next_cursor(connection([], "same"), {"same"})

    def test_excluded_repository_affects_only_languages(self):
        def fake(query, variables):
            if query == profile.ACTIVITY_QUERY:
                calendar = days()
                return {"user": {"contributionsCollection": {
                    "totalCommitContributions": 100, "totalIssueContributions": 2,
                    "totalPullRequestContributions": 3,
                    "contributionCalendar": {"weeks": [{"contributionDays": [
                        {"date": d["date"], "contributionCount": d["count"]} for d in calendar
                    ]}]},
                }}}
            if query == profile.LANGUAGES_QUERY:
                raise AssertionError("Excluded repository language pages must not be fetched")
            if variables["cursor"] is None:
                return {"user": {"repositories": connection([
                    {"name": "one", "stargazerCount": 2, "forkCount": 1,
                     "languages": languages("C", 20)},
                ], "next")}}
            return {"user": {"repositories": connection([
                {"name": "t3LM", "stargazerCount": 8, "forkCount": 3,
                 "languages": languages("C", 1_000_000, "excluded-next")},
                {"name": "two", "stargazerCount": 1, "forkCount": 0,
                 "languages": languages("Python", 30)},
            ])}}

        result = profile.fetch_snapshot("test", NOW, fake)
        self.assertEqual(result["languages"], {"C": 20, "Python": 30})
        self.assertEqual((result["publicRepos"], result["stars"], result["forks"]), (3, 11, 4))
        self.assertEqual((result["commits"], result["pullRequests"]), (100, 3))
        self.assertEqual(result["contributions"], sum(d["count"] for d in days()))
        self.assertEqual(result["languageExcludedRepos"], ["T3lm"])
        self.assertEqual([r[2] for r in profile.language_rows(result["languages"])], [0.6, 0.4])

    def test_language_denominator_includes_other(self):
        rows = profile.language_rows({"C": 50, "Python": 30, "Go": 10, "Rust": 10}, limit=3)
        self.assertEqual(rows[-1], ("Other", 20, 0.2))
        self.assertAlmostEqual(sum(r[2] for r in rows), 1)

    def test_absence_is_different_from_zero_and_svg_escapes_names(self):
        s = snapshot()
        s.update(commits=None, languages=None)
        unavailable = profile.render_runtime(s)
        self.assertIn("awaiting first API sync", unavailable)
        self.assertIn("ENODATA", unavailable)
        self.assertIn(">--</text>", unavailable)
        s.update(commits=0, languages={"C & <asm>": 50})
        zero = profile.render_runtime(s)
        self.assertNotIn("awaiting first API sync", zero)
        self.assertIn("C &amp; &lt;asm&gt;", zero)
        ET.fromstring(zero)

    def test_failed_refresh_preserves_existing_snapshot_and_image(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target, image = root / "snapshot.json", root / "runtime.svg"
            target.write_text(json.dumps(snapshot()))
            image.write_text("last-good-image")
            before = target.read_bytes()
            with patch.dict(os.environ, {"GH_TOKEN": "test"}), patch.object(
                profile, "fetch_snapshot", side_effect=RuntimeError("API unavailable")
            ):
                self.assertEqual(profile.main(["--refresh", "--snapshot", str(target), "--output", str(root)]), 1)
            self.assertEqual(target.read_bytes(), before)
            self.assertEqual(image.read_text(), "last-good-image")

    def test_render_only_does_not_fetch_and_all_images_are_valid(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / "snapshot.json"
            target.write_text(json.dumps(snapshot()))
            with patch.object(profile, "graphql", side_effect=AssertionError("Unexpected network")):
                self.assertEqual(profile.main(["--snapshot", str(target), "--output", str(root)]), 0)
            for name in ["header-machine.svg", "toolchain.svg", "runtime.svg", "header-machine-mobile.svg", "toolchain-mobile.svg", "runtime-mobile.svg",
                         "interests.svg", "interests-mobile.svg", "traces-heading.svg", "traces-heading-mobile.svg",
                         "projects-heading.svg", "projects-heading-mobile.svg"]:
                ET.parse(root / name)

    def test_incomplete_and_inconsistent_calendars_are_rejected(self):
        with self.assertRaises(RuntimeError):
            profile.calendar_snapshot("<html>rate limited</html>", NOW)
        s = snapshot()
        s["days"].pop(100)
        with self.assertRaises(ValueError):
            profile.validate_snapshot(s)
        s = snapshot()
        s["contributions"] += 1
        with self.assertRaises(ValueError):
            profile.validate_snapshot(s)

    def test_streak_uses_yesterday_when_today_has_no_activity(self):
        self.assertEqual(profile.streaks([{"count": 1}, {"count": 3}, {"count": 0}]), (2, 2))
        self.assertEqual(profile.streaks([{"count": 1}, {"count": 0}, {"count": 0}]), (0, 1))


if __name__ == "__main__":
    unittest.main()
