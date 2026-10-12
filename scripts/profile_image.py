#!/usr/bin/env python3
"""Render the profile's machine interface; only --refresh talks to GitHub.

No dependencies. SVGs contain a dated snapshot, never client-side requests or
credentials. A failed fetch leaves the last successful snapshot untouched.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from html import escape
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
LOGIN = "m7md-d"
# Exclude these repositories from language byte totals only.
LANGUAGE_EXCLUDED_REPOS = frozenset({"T3lm"})
BG, PANEL, LINE = "#090B0D", "#111519", "#30373E"
FG, MUTED, SOFT = "#E1E5E8", "#909AA3", "#C0C8CF"
SHADES = ["#161C21", "#35414A", "#62717C", "#98A7B2", "#DDE5EB"]

ACTIVITY_QUERY = """
query Profile($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      totalCommitContributions totalIssueContributions totalPullRequestContributions
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}
"""
REPOS_QUERY = """
query Repositories($login: String!, $cursor: String) {
  user(login: $login) {
    repositories(first: 100, after: $cursor, privacy: PUBLIC,
                 ownerAffiliations: OWNER, isFork: false) {
      pageInfo { hasNextPage endCursor }
      nodes {
        name stargazerCount forkCount
        languages(first: 100, orderBy: {field: SIZE, direction: DESC}) {
          pageInfo { hasNextPage endCursor }
          edges { size node { name } }
        }
      }
    }
  }
}
"""
LANGUAGES_QUERY = """
query Languages($login: String!, $name: String!, $cursor: String) {
  repository(owner: $login, name: $name) {
    languages(first: 100, after: $cursor, orderBy: {field: SIZE, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      edges { size node { name } }
    }
  }
}
"""


def graphql(query, variables, token):
    request = Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json", "User-Agent": "m7md-profile-image"},
    )
    try:
        with urlopen(request, timeout=40) as response:
            payload = json.load(response)
    except HTTPError as error:
        # Do not echo HTTP bodies or credentials into workflow logs.
        raise RuntimeError(f"GitHub returned HTTP {error.code}; snapshot preserved") from None
    except URLError:
        raise RuntimeError("GitHub could not be reached; snapshot preserved") from None
    if payload.get("errors") or not payload.get("data"):
        raise RuntimeError("GitHub GraphQL query failed; snapshot preserved")
    return payload["data"]


def next_cursor(connection, seen):
    page = connection["pageInfo"]
    if not page["hasNextPage"]:
        return None
    cursor = page["endCursor"]
    if not cursor or cursor in seen:
        raise RuntimeError("GitHub pagination did not advance; snapshot preserved")
    seen.add(cursor)
    return cursor


def fetch_snapshot(token, now=None, fetch=None):
    """Fetch all public owned non-fork repositories, including archived ones."""
    now = now or datetime.now(timezone.utc)
    fetch = fetch or (lambda q, v: graphql(q, v, token))
    start = datetime.combine(now.date() - timedelta(days=364), datetime.min.time(), timezone.utc)
    variables = {"login": LOGIN, "from": start.isoformat(), "to": now.isoformat()}
    user = fetch(ACTIVITY_QUERY, variables).get("user")
    if not user:
        raise RuntimeError("GitHub user was not found; snapshot preserved")
    activity = user["contributionsCollection"]
    days = sorted(
        ({"date": d["date"], "count": d["contributionCount"]}
         for w in activity["contributionCalendar"]["weeks"] for d in w["contributionDays"]
         if start.date().isoformat() <= d["date"] <= now.date().isoformat()),
        key=lambda d: d["date"],
    )
    languages, repos = Counter(), []
    excluded_languages = {name.casefold() for name in LANGUAGE_EXCLUDED_REPOS}
    cursor, seen = None, set()
    while True:
        user = fetch(REPOS_QUERY, {"login": LOGIN, "cursor": cursor}).get("user")
        if not user:
            raise RuntimeError("GitHub repositories unavailable; snapshot preserved")
        connection = user["repositories"]
        for repo in connection["nodes"]:
            repos.append(repo)
            if repo["name"].casefold() in excluded_languages:
                continue
            lang_connection, lang_seen = repo["languages"], set()
            while True:
                for edge in lang_connection["edges"]:
                    languages[edge["node"]["name"]] += edge["size"]
                lang_cursor = next_cursor(lang_connection, lang_seen)
                if lang_cursor is None:
                    break
                repository = fetch(LANGUAGES_QUERY, {
                    "login": LOGIN, "name": repo["name"], "cursor": lang_cursor,
                }).get("repository")
                if not repository:
                    raise RuntimeError("GitHub language page unavailable; snapshot preserved")
                lang_connection = repository["languages"]
        cursor = next_cursor(connection, seen)
        if cursor is None:
            break
    snapshot = {
        "schema": 1, "login": LOGIN, "source": "github-graphql",
        "updatedAt": now.isoformat(timespec="seconds"),
        "from": start.date().isoformat(), "to": now.date().isoformat(),
        "contributions": sum(d["count"] for d in days), "days": days,
        "commits": activity["totalCommitContributions"],
        "issues": activity["totalIssueContributions"],
        "pullRequests": activity["totalPullRequestContributions"],
        "publicRepos": len(repos),
        "stars": sum(r["stargazerCount"] for r in repos),
        "forks": sum(r["forkCount"] for r in repos),
        "languages": dict(languages),
        "languageExcludedRepos": sorted(LANGUAGE_EXCLUDED_REPOS),
    }
    validate_snapshot(snapshot)
    return snapshot


class PublicCalendar(HTMLParser):
    """Import an actual public calendar for a truthful initial preview."""
    def __init__(self):
        super().__init__()
        self.ids, self.days, self.tip, self.text = {}, {}, None, []

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == "td" and attrs.get("data-date") and attrs.get("id"):
            self.ids[attrs["id"]] = attrs["data-date"]
        if tag == "tool-tip":
            self.tip, self.text = attrs.get("for"), []

    def handle_data(self, content):
        if self.tip:
            self.text.append(content)

    def handle_endtag(self, tag):
        if tag == "tool-tip" and self.tip:
            text = "".join(self.text).strip()
            day = self.ids.get(self.tip)
            match = re.match(r"([\d,]+) contributions?\b", text)
            if day and (match or text.startswith("No contributions")):
                self.days[day] = int(match[1].replace(",", "")) if match else 0
            self.tip = None


def calendar_snapshot(html, now=None):
    parser = PublicCalendar()
    parser.feed(html)
    days = [{"date": d, "count": n} for d, n in sorted(parser.days.items())]
    if len(days) != 365:
        raise RuntimeError("Expected a complete 365-day public calendar; no data was written")
    snapshot = {
        "schema": 1, "login": LOGIN, "source": "github-public-calendar",
        "updatedAt": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "from": days[0]["date"], "to": days[-1]["date"], "days": days,
        "contributions": sum(d["count"] for d in days),
        **dict.fromkeys(["commits", "issues", "pullRequests", "publicRepos", "stars", "forks", "languages"]),
    }
    validate_snapshot(snapshot)
    return snapshot


def validate_snapshot(snapshot):
    days = snapshot["days"]
    if snapshot["login"] != LOGIN or not days:
        raise ValueError("Snapshot belongs to a different profile or has no calendar")
    expected = date.fromisoformat(snapshot["from"])
    for day in days:
        if date.fromisoformat(day["date"]) != expected:
            raise ValueError("Calendar has missing, duplicate, or unordered days")
        if type(day["count"]) is not int or day["count"] < 0:
            raise ValueError("Invalid contribution count")
        expected += timedelta(days=1)
    if days[-1]["date"] != snapshot["to"] or not 1 <= len(days) <= 365:
        raise ValueError("Invalid calendar range")
    if sum(d["count"] for d in days) != snapshot["contributions"]:
        raise ValueError("Calendar and contribution total disagree")
    for field in ["commits", "issues", "pullRequests", "publicRepos", "stars", "forks"]:
        value = snapshot[field]
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError("Invalid metric")
    if snapshot["languages"] is not None:
        if not isinstance(snapshot["languages"], dict):
            raise ValueError("Invalid languages")
        for name, size in snapshot["languages"].items():
            if not isinstance(name, str) or type(size) is not int or size < 0:
                raise ValueError("Invalid language bytes")


def streaks(days):
    longest = run = 0
    for day in days:
        run = run + 1 if day["count"] else 0
        longest = max(longest, run)
    recent = days[:-1] if days and not days[-1]["count"] else days
    current = 0
    for day in reversed(recent):
        if not day["count"]:
            break
        current += 1
    return current, longest


def language_rows(languages, limit=6):
    """Include Other in the denominator; displayed percentages sum to 100%."""
    ordered = sorted(((n, v) for n, v in languages.items() if v), key=lambda item: (-item[1], item[0]))
    if len(ordered) > limit:
        ordered = ordered[:limit - 1] + [("Other", sum(v for _, v in ordered[limit - 1:]))]
    total = sum(languages.values())
    return [(name, size, size / total) for name, size in ordered] if total else []


def text(x, y, value, size=16, fill=FG, weight="400", extra=""):
    return (f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill}" '
            f'font-weight="{weight}" {extra}>{escape(str(value))}</text>')


def rect(x, y, w, h, fill=PANEL, stroke=LINE, extra=""):
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{fill}" stroke="{stroke}" {extra}/>'


def line(x1, y1, x2, y2, stroke=LINE):
    return f'<path d="M{x1} {y1}H{x2}" stroke="{stroke}"/>' if y1 == y2 else f'<path d="M{x1} {y1}L{x2} {y2}" stroke="{stroke}"/>'


def svg(height, title, description, elements, width=1200):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="title description">'
            f'<title id="title">{escape(title)}</title><desc id="description">{escape(description)}</desc>'
            f'<g font-family="monospace">{rect(1, 1, width - 2, height - 2, BG)}'
            + "".join(elements) + '</g></svg>\n')


def render_header():
    parts = [text(28, 30, "m7md-d@profile ~", 15, SOFT),
             text(1168, 30, "ELF / IDENTITY MAP", 12, MUTED, extra='text-anchor="end"'),
             line(1, 44, 1199, 44), text(28, 82, "00000000", 12, MUTED),
             text(142, 82, ".identity", 15, SOFT), text(139, 145, LOGIN, 60, FG, "700"),
             text(142, 188, "Exploring low-level systems & security.", 21, SOFT),
             text(28, 240, "00000040", 12, MUTED),
             text(142, 240, "6d 37 6d 64 2d 64  00 00", 16, SOFT),
             line(738, 68, 738, 260),
             text(774, 85, "SECTION     OFFSET    FLAGS", 13, MUTED)]
    for i, (name, offset, flags) in enumerate([(".text", "0x0000", "r-x"), (".rodata", "0x0100", "r--"), (".data", "0x0200", "rw-")]):
        y = 124 + i * 40
        parts += [text(774, y, name, 18), text(937, y, offset, 16, SOFT), text(1100, y, flags, 16, SOFT)]
    parts += [line(1, 278, 1199, 278), text(28, 305, "$", 16, SOFT),
              text(54, 305, "systems / reverse engineering / security", 14, MUTED),
              rect(1126, 290, 10, 16, FG, FG)]
    return svg(326, "m7md-d — Exploring low-level systems & security.",
               "A monochrome terminal and binary interface. Section offsets are a visual identity map, not measurements of a program.", parts)


def badge(x, y, width, label, primary=False, size=18):
    return [rect(x, y, width, 46, FG if primary else PANEL),
            text(x + width / 2, y + 29, label, size, BG if primary else SOFT,
                 "700" if primary else "400", extra='text-anchor="middle"')]


def render_toolchain():
    parts = [text(28, 35, "Languages", 19), text(662, 35, "Tools", 19),
             line(28, 53, 570, 53), line(662, 53, 1170, 53)]
    parts += badge(28, 77, 98, "C", primary=True, size=28)
    for x, width, label in [(28, 148, "Python"), (188, 200, "TypeScript"), (400, 100, "Go")]:
        parts += badge(x, 141, width, label)
    for x, y, width, label in [(662, 77, 152, "Git"), (826, 77, 160, "Docker"),
                              (998, 77, 172, "Linux"), (662, 141, 152, "Bash"),
                              (826, 141, 160, "GDB"), (998, 141, 172, "Ghidra"),
                              (662, 205, 152, "objdump"), (826, 205, 160, "Nmap"),
                              (998, 205, 172, "Podman")]:
        parts += badge(x, y, width, label)
    return svg(282, "Languages: C, Python, TypeScript, Go. Tools: Git, Docker, Linux, Bash, GDB, Ghidra, objdump, Nmap, Podman.",
               "Language and tool badges. C is displayed first. No proficiency ratings.", parts)


def render_header_mobile():
    parts = [text(20, 29, "m7md-d@profile ~", 17, SOFT), line(1, 44, 599, 44),
             text(20, 76, "00000000 / .identity", 16, MUTED),
             text(18, 139, LOGIN, 58, FG, "700"),
             text(20, 182, "Exploring low-level systems", 22, SOFT),
             text(20, 214, "& security.", 22, SOFT),
             text(20, 255, "6d 37 6d 64 2d 64  00 00", 20, MUTED),
             line(20, 277, 580, 277), text(20, 303, "SECTION", 15, MUTED),
             text(225, 303, "OFFSET", 15, MUTED), text(487, 303, "FLAGS", 15, MUTED)]
    for i, (name, offset, flags) in enumerate([(".text", "0x0000", "r-x"), (".rodata", "0x0100", "r--"), (".data", "0x0200", "rw-")]):
        y = 334 + 30 * i
        parts += [text(20, y, name, 20), text(225, y, offset, 20, SOFT), text(487, y, flags, 20, SOFT)]
    return svg(417, "m7md-d — Exploring low-level systems & security.",
               "Mobile identity map. Section offsets are decorative.", parts, width=600)


def render_interests(mobile=False):
    rows = [("systems", ["operating systems · Linux internals", "processes · memory"]),
            ("reversing", ["ELF · disassembly · binary analysis", "reconstruction"]),
            ("security", ["program behaviour · isolation", "system boundaries"]),
            ("building", ["tools · automation", "practical solutions"])]
    width = 600 if mobile else 1200
    margin = 20 if mobile else 28
    parts = [text(margin, 34, ".text / interests", 22 if mobile else 19),
             line(1, 53, width - 1, 53)]
    for i, (label, details) in enumerate(rows):
        if mobile:
            y = 90 + i * 101
            parts += [text(margin, y, label, 21, FG, "700"),
                      text(margin, y + 30, details[0], 20, SOFT),
                      text(margin, y + 58, details[1], 20, SOFT)]
        else:
            y = 96 + i * 43
            parts += [text(margin, y, label, 19, FG, "700"),
                      text(212, y, " · ".join(details), 18, SOFT)]
    return svg(480 if mobile else 254, "Interests: systems, reverse engineering, security and building tools.",
               "Operating systems and Linux internals; binary analysis and reconstruction; program behaviour and isolation; tools and automation.",
               parts, width=width)


def render_section_heading(section, label, mobile=False):
    width = 600 if mobile else 1200
    return svg(72 if mobile else 64, f"{section} / {label}",
               f"{label.capitalize()} section.",
               [text(20 if mobile else 28, 44 if mobile else 40,
                     f"{section} / {label}", 22 if mobile else 19)], width=width)


def render_toolchain_mobile():
    parts = [text(20, 34, "Languages", 22), line(20, 53, 580, 53)]
    parts += badge(20, 77, 98, "C", primary=True, size=28)
    for x, width, label in [(20, 148, "Python"), (180, 200, "TypeScript"), (392, 100, "Go")]:
        parts += badge(x, 141, width, label, size=20)
    parts += [text(20, 241, "Tools", 22), line(20, 260, 580, 260)]
    for x, y, width, label in [(20, 284, 176, "Git"), (208, 284, 176, "Docker"),
                              (396, 284, 184, "Linux"), (20, 348, 176, "Bash"),
                              (208, 348, 176, "GDB"), (396, 348, 184, "Ghidra"),
                              (20, 412, 176, "objdump"), (208, 412, 176, "Nmap"),
                              (396, 412, 184, "Podman")]:
        parts += badge(x, y, width, label, size=20)
    return svg(482, "Languages: C, Python, TypeScript, Go. Tools: Git, Docker, Linux, Bash, GDB, Ghidra, objdump, Nmap, Podman.",
               "Language and tool badges, with C first.", parts, width=600)


def render_runtime_mobile(snapshot):
    validate_snapshot(snapshot)
    days = snapshot["days"]
    current, longest = streaks(days)
    parts = [text(20, 34, ".data / runtime snapshot", 22), line(1, 53, 599, 53)]
    metrics = [("CONTRIBUTIONS", snapshot["contributions"], "calendar events / 365d"),
               ("COMMIT CONTRIBUTIONS", snapshot["commits"], "GitHub-counted / 365d" if snapshot["commits"] is not None else "awaiting API sync"),
               ("ACTIVE DAYS", sum(d["count"] > 0 for d in days), "days with activity"),
               ("LONGEST STREAK", longest, f"current: {current}d")]
    for i, (label, value, detail) in enumerate(metrics):
        x, y = 20 + (i % 2) * 290, 74 + (i // 2) * 148
        parts += [rect(x, y, 270, 130), text(x + 15, y + 27, label, 16, MUTED),
                  text(x + 15, y + 79, metric(value), 43, FG, "700"),
                  text(x + 15, y + 111, detail, 15, MUTED)]
    parts += [text(20, 397, "0x0100 .activity / 365d", 21, SOFT),
              text(20, 426, f'{snapshot["from"]} .. {snapshot["to"]}', 17, MUTED)]
    first = date.fromisoformat(days[0]["date"])
    padding = (first.weekday() + 1) % 7
    peak = max(d["count"] for d in days)
    for i, day in enumerate(days):
        level = 0 if not day["count"] else min(4, 1 + int((day["count"] / peak) ** 0.5 * 3))
        index = i + padding
        parts.append(f'<g><title>{escape(day["date"])}: {day["count"]} contributions</title>'
                     + rect(29 + (index // 7) * 10, 450 + (index % 7) * 10, 7, 7, SHADES[level], SHADES[level]) + '</g>')
    parts += [text(20, 554, "GitHub contribution calendar", 18, MUTED),
              line(20, 579, 580, 579), text(20, 613, "0x0200 .languages", 21, SOFT),
              text(20, 642, "Public owned repositories / no forks", 16, MUTED)]
    if snapshot["languages"] is None:
        parts += [rect(20, 666, 560, 162), text(40, 707, "ENODATA", 24, SOFT),
                  text(40, 751, "Language bytes await the first", 20, MUTED),
                  text(40, 785, "authenticated GitHub sync.", 20, MUTED)]
    else:
        rows = language_rows(snapshot["languages"])
        if not rows:
            parts.append(text(20, 704, "No tracked language bytes.", 20, MUTED))
        for i, (name, size, fraction) in enumerate(rows):
            y = 685 + i * 55
            parts += [text(20, y, name[:22], 21),
                      text(580, y, f"{fraction:.1%}", 20, SOFT, extra='text-anchor="end"'),
                      rect(20, y + 14, 560, 5, PANEL, PANEL),
                      rect(20, y + 14, round(560 * fraction, 2), 5, SOFT, SOFT)]
    stamp = datetime.fromisoformat(snapshot["updatedAt"]).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    state = "PARTIAL" if snapshot["source"] == "github-public-calendar" else "SYNCED"
    parts += [text(20, 1003, "Byte share describes code, not proficiency.", 16, MUTED),
              line(1, 1024, 599, 1024), text(20, 1051, f"{state} / {stamp}", 16, SOFT),
              text(20, 1080, f'repos {metric(snapshot["publicRepos"])} / stars {metric(snapshot["stars"])} / PRs {metric(snapshot["pullRequests"])}', 16, MUTED)]
    return svg(1102, "GitHub activity and language snapshot for m7md-d",
               "Mobile activity view. Missing metrics are dashes; language share is byte-based.", parts, width=600)


def metric(value):
    return f"{value:,}" if value is not None else "--"


def render_runtime(snapshot):
    validate_snapshot(snapshot)
    days = snapshot["days"]
    current, longest = streaks(days)
    parts = [text(28, 33, ".data / runtime snapshot", 19),
             text(1170, 33, "GITHUB / 365-DAY WINDOW", 12, MUTED, extra='text-anchor="end"'),
             line(1, 50, 1199, 50)]
    metrics = [("CONTRIBUTIONS", snapshot["contributions"], "calendar events / 365d"),
               ("COMMIT CONTRIBUTIONS", snapshot["commits"], "GitHub-counted / 365d" if snapshot["commits"] is not None else "awaiting first API sync"),
               ("ACTIVE DAYS", sum(d["count"] > 0 for d in days), "days with contributions"),
               ("LONGEST STREAK", longest, f"current streak: {current}d")]
    for i, (label, value, detail) in enumerate(metrics):
        x = 28 + i * 292
        parts += [rect(x, 68, 268, 114), text(x + 16, 94, label, 12, MUTED),
                  text(x + 16, 140, metric(value), 36, FG, "700"),
                  text(x + 16, 165, detail, 11, MUTED)]
    parts += [text(28, 217, "0x0100  .activity", 15, SOFT),
              text(1170, 217, f'{snapshot["from"]} .. {snapshot["to"]}', 12, MUTED, extra='text-anchor="end"')]
    first = date.fromisoformat(days[0]["date"])
    padding = (first.weekday() + 1) % 7
    peak = max(d["count"] for d in days)
    for i, day in enumerate(days):
        n = day["count"]
        level = 0 if not n else min(4, 1 + int((n / peak) ** 0.5 * 3))
        index = i + padding
        x, y = 76 + (index // 7) * 20, 242 + (index % 7) * 20
        parts.append(f'<g><title>{escape(day["date"])}: {n} contributions</title>{rect(x, y, 15, 15, SHADES[level], SHADES[level])}</g>')
    for row, label in [(1, "Mon"), (3, "Wed"), (5, "Fri")]:
        parts.append(text(28, 254 + row * 20, label, 11, MUTED))
    parts += [text(28, 405, "Calendar events include commits, PRs, issues and other counted contributions.", 12, MUTED)]
    for i, color in enumerate(SHADES):
        parts.append(rect(1047 + i * 24, 391, 15, 15, color, color))
    parts += [line(28, 425, 1170, 425), text(28, 456, "0x0200  .languages", 15, SOFT),
              text(1170, 456, "PUBLIC OWNED REPOS / NO FORKS / BYTE SHARE", 11, MUTED, extra='text-anchor="end"')]
    if snapshot["languages"] is None:
        parts += [rect(28, 476, 1142, 89), text(48, 509, "ENODATA", 18, SOFT),
                  text(48, 541, "Language bytes are awaiting the first authenticated GitHub sync.", 15, MUTED)]
    else:
        rows = language_rows(snapshot["languages"])
        if not rows:
            parts.append(text(28, 511, "No tracked language bytes in eligible repositories.", 15, MUTED))
        for i, (name, size, fraction) in enumerate(rows):
            x, y = 28 + (i % 2) * 584, 491 + (i // 2) * 38
            parts += [text(x, y, name[:22], 14),
                      text(x + 530, y, f"{fraction:.1%}", 13, SOFT, extra='text-anchor="end"'),
                      rect(x, y + 9, 530, 4, PANEL, PANEL),
                      rect(x, y + 9, round(530 * fraction, 2), 4, SOFT, SOFT)]
    parts += [text(28, 610, "Language share describes tracked code, not proficiency.", 12, MUTED),
              line(1, 630, 1199, 630)]
    state = "PARTIAL" if snapshot["source"] == "github-public-calendar" else "SYNCED"
    stamp = datetime.fromisoformat(snapshot["updatedAt"]).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    parts += [text(28, 654, f"{state} / fetched {stamp}", 12, SOFT),
              text(1170, 654, f'repos {metric(snapshot["publicRepos"])} / stars {metric(snapshot["stars"])} / PRs {metric(snapshot["pullRequests"])}', 12, MUTED, extra='text-anchor="end"')]
    return svg(676, "GitHub activity and language snapshot for m7md-d",
               f'{snapshot["contributions"]} contributions from {snapshot["from"]} to {snapshot["to"]}. '
               'Unavailable values are dashes, not zeros. Language percentages describe bytes in public owned non-fork repositories, not skill.', parts)


def save_snapshot(snapshot, target):
    validate_snapshot(snapshot)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(target)


def write_assets(snapshot, output):
    # Construct and validate everything before replacing existing images.
    import xml.etree.ElementTree as ET
    images = {"header-machine.svg": render_header(), "toolchain.svg": render_toolchain(),
              "runtime.svg": render_runtime(snapshot),
              "header-machine-mobile.svg": render_header_mobile(),
              "toolchain-mobile.svg": render_toolchain_mobile(),
              "runtime-mobile.svg": render_runtime_mobile(snapshot),
              "interests.svg": render_interests(),
              "interests-mobile.svg": render_interests(mobile=True),
              "traces-heading.svg": render_section_heading(".rodata", "traces"),
              "traces-heading-mobile.svg": render_section_heading(".rodata", "traces", mobile=True),
              "projects-heading.svg": render_section_heading(".build", "tools for real problems"),
              "projects-heading-mobile.svg": render_section_heading(".build", "tools for real problems", mobile=True)}
    for content in images.values():
        ET.fromstring(content)
    output.mkdir(parents=True, exist_ok=True)
    for filename, content in images.items():
        target = output / filename
        temporary = target.with_suffix(".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(target)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--refresh", action="store_true")
    mode.add_argument("--calendar-html", type=Path)
    parser.add_argument("--snapshot", type=Path, default=ROOT / "data/profile.json")
    parser.add_argument("--output", type=Path, default=ROOT / "assets")
    args = parser.parse_args(argv)
    try:
        if args.refresh:
            token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
            if not token:
                raise RuntimeError("No GitHub token available; run the repository's Refresh profile image workflow")
            snapshot = fetch_snapshot(token)
        elif args.calendar_html:
            snapshot = calendar_snapshot(args.calendar_html.read_text(encoding="utf-8"))
        else:
            snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
        write_assets(snapshot, args.output)
        if args.refresh or args.calendar_html:
            save_snapshot(snapshot, args.snapshot)
    except (RuntimeError, ValueError, KeyError, OSError) as error:
        print(f"Profile update failed: {error}", file=sys.stderr)
        return 1
    print(f"Profile SVGs rendered from {snapshot['source']}; fetched {snapshot['updatedAt']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
