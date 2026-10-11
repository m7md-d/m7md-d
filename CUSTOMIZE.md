# Machine identity — editing and updates

The current experiment is `README.preview.md`. The original `README.md` has not
been replaced. The portfolio is outside this experiment.

## Files

- `scripts/profile_image.py`: palette, copy, layouts and GitHub data collection.
- `assets/header-machine.svg`: terminal / ELF identity map.
- `assets/interests.svg`: systems, reverse engineering, security and building tools.
- `assets/toolchain.svg`: language and tool badges, with C first.
- `assets/runtime.svg`: dated GitHub activity and language snapshot.
- `assets/traces-heading.svg` and `assets/projects-heading.svg`: repository section dividers.
- `assets/*-mobile.svg`: layouts selected below 600px using `<picture>`.
- `data/profile.json`: public snapshot used to render the images.
- `.github/workflows/profile-image.yml`: refresh every six hours and on demand.

The earlier HTB experiments are unused by the current README draft.

## Change the copy or theme

Edit the render functions and colour constants in `scripts/profile_image.py`.
Generated SVGs are overwritten by the next update; edit the generator for
persistent changes. Interests and section headings also live in the generator. Repository links and their short labels live in
`README.preview.md`. Reference meanings are documented in `BRAND-STUDY.md`, outside the public profile.

Regenerate locally without any network access:

```bash
python3 scripts/profile_image.py
```

## Enable updates when adopting the draft

1. Put the selected README and its assets, script, tests, snapshot and workflow
   in the public `m7md-d/m7md-d` repository, on its `main` branch.
2. Open Actions → Refresh profile image → Run workflow for the first update.
3. The scheduled workflow repeats at 00:17, 06:17, 12:17 and 18:17 UTC
   (03:17, 09:17, 15:17 and 21:17 in Riyadh). GitHub can delay scheduled jobs.

The workflow uses GitHub's supplied repository token. If a repository or account
policy prevents that token from reading the public GraphQL fields, it fails
without replacing the current snapshot. `PROFILE_TOKEN` is an optional Actions
secret for a token that can read those public fields; no private-repository
permission is required. GitHub's normal repository token still saves the files.
The token is never included in the SVG or saved snapshot.

If branch protection prevents the workflow from pushing generated files, the
save step fails visibly in Actions. Public scheduled workflows may be disabled
by GitHub after prolonged repository inactivity; they can be re-enabled there.

## What the numbers measure

- Contribution calendar: 365 UTC calendar days ending at the snapshot date.
  Includes counted commits, PRs, issues and other GitHub contributions.
- Commit contributions: `totalCommitContributions` for the same window. Follows
  GitHub's contribution rules; it is not an all-time total of every Git commit.
- Active days: days with at least one calendar contribution.
- Longest streak: longest consecutive active run inside that window.
- Current streak: ends today, or yesterday if today has no activity yet; measured
  at the snapshot time, not the time the reader opens the image.
- Repositories / stars / forks: public owned non-fork repositories, including
  archived repositories, across all pages.
- PRs / issues: contributions in the same 365-day window, not lifetime totals.
- Languages: summed GitHub language bytes in those repositories. Includes Other
  when more than six languages are present; percentages use all tracked bytes.
  Generated and vendored files follow GitHub Linguist's classification.
  Language share is not a skill rating or proof of manually authored code.

`T3lm` is excluded from language totals and percentage denominators only.
Its contributions, repository count, stars and forks are still counted.
Change `LANGUAGE_EXCLUDED_REPOS` in `scripts/profile_image.py` to edit this list;
repository names are compared without regard to letter case. Each authenticated
snapshot records the applied list in `languageExcludedRepos`. Changing the list
requires a fresh API collection; rendering an old aggregate cannot remove a
repository's bytes retroactively.

The initial snapshot came from the actual public contribution calendar.
Commit counts, languages and repository aggregates are unavailable locally
because the unauthenticated API quota was exhausted. They are `null` in the
snapshot and appear as `--` / `ENODATA`, not zeros or invented sample values.
An authenticated refresh replaces them with real data.

## Checks

```bash
python3 -m unittest discover -s tests -v
```

Checks cover repository/language pagination, percentage totals, missing data,
SVG escaping, stale-data preservation, calendar validation and streak rules.
The authenticated GitHub query and hosted workflow have not been run in this
local environment; their first end-to-end verification occurs in Actions.

## References

- [GitHub contribution fields](https://docs.github.com/en/graphql/reference/users)
- [Repository languages](https://docs.github.com/en/graphql/reference/repos)
- [GitHub contribution rules](https://docs.github.com/en/account-and-profile/reference/profile-contributions-reference)
- [GITHUB_TOKEN](https://docs.github.com/en/actions/tutorials/authenticate-with-github_token)
- [Supported Markdown images](https://docs.github.com/en/get-started/writing-on-github/getting-started-with-writing-and-formatting-on-github/basic-writing-and-formatting-syntax)
