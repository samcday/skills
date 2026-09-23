---
name: lore
description: Search and read Linux kernel mailing-list archives (LKML, lore.kernel.org) from local git mirrors of linux-arm-msm, phone-devel, devicetree, linux-arm-kernel, dri-devel and other qcom/phone lists. Use for prior art, review feedback, patch-series revisions, what maintainers said, fetching a series for b4, or any lore link or Message-ID. lore.kernel.org's web UI blocks agents, so never WebFetch it.
---

# lore: local LKML search

`lore` (on PATH; the source is `lore.py` beside this file) searches bare git
mirrors of lore.kernel.org inboxes in `~/lore`. Each mirror covers its list's
whole history, and `lore-sync.timer` (systemd user unit) refreshes them every
30 minutes. Don't use lore.kernel.org's web UI: it returns 403 to curl and
WebFetch and an Anubis challenge to browsers. Its git endpoints are what the
mirrors fetch from.

`lore status` shows the mirrored lists, message counts and the last sync;
`~/lore/lists` is the list config. A message cross-posted to several lists is
indexed once, and `lore show` prints every list it reached.

## Commands

A MID argument can be a bare Message-ID, `<id>`, or a lore.kernel.org,
lkml.kernel.org or patch.msgid.link URL.

| Command | Does |
|---|---|
| `lore search QUERY` | One line per message, newest first: date, author, subject, `<Message-ID>`. `-n N` (default 25), `-t` one line per thread with hit and message counts, `-C` matching body lines, `--rank` BM25 relevance, `--oldest`, `--json` |
| `lore thread MID` | Thread tree; `*` marks MID. `--full` prints every message (From, Date, Subject and body) with each quoted block cut to its last 3 lines (`-q N`, `-q -1` keeps all); `--skip-patches` leaves only the discussion |
| `lore show MID...` | Headers, `Link:`, `Lists:` and the decoded body. `-q N` trims quoted blocks; `--raw` prints the RFC 822 message (use its headers to build a reply's In-Reply-To, References, To and Cc) |
| `lore series MID` | Every revision of MID's series, matched by cover title and b4 `change-id:` |
| `lore mbox MID [-t]` | mboxrd of the message or whole thread, for `b4 am -m -` or `git am` |
| `lore sync [LIST...]` | Fetch from lore and index now (seconds when little changed) |
| `lore add LIST...` | Mirror more lists; names are lore's inbox names (`https://lore.kernel.org/<name>/`) |

Search exits 1 when nothing matches. Output is compact on purpose: search
first, then `thread` or `show` the few Message-IDs that matter.

## Query syntax

Lore's prefixes, over an SQLite FTS5 index. Terms are ANDed; `OR`, `NOT`,
`-term`, parentheses, `"phrases"` and a trailing `*` prefix match work.

| Prefix | Field |
|---|---|
| bare word | subject, unquoted body and diff file names |
| `s:` `b:` `bs:` | subject, unquoted body, both |
| `f:` `t:` `c:` `tc:` `a:` | From, To, Cc, To or Cc, any address |
| `dfn:` `dfb:` `dfa:` `diff:` | diff file names, added lines, removed lines, all three |
| `m:` | exact Message-ID |
| `l:` | list: `linux-arm-msm`, `phone-devel`, `dt`, `lakml`, ...; `l:a,b` = either |
| `d:` `rt:` | sent or received date: `2024-01-01..2024-06-30`, `2024`, `2.weeks.ago..`, `3mo..`, `today` |
| `is:` | `patch`, `cover`, `reply`, `pull` (git pull request) or `root` (thread starter) |

- Punctuation splits words, so anything containing it is a phrase:
  `b:"qcom,sm6115-pinctrl"`, `f:konradybcio@kernel.org`, `s:"sm8550-mtp"`,
  `dfn:arch/arm64/boot/dts/qcom/sdm845-*`. Case is ignored.
- `m:`, `l:`, `d:`, `rt:` and `is:` filter the whole query: use them at the
  top level, not inside `OR` or parentheses.
- Quoted reply text (`>` lines) is not indexed. To find review of a hunk,
  search for the patch (`dfb:`) and read its thread.
- The From of a B4 Relay message is `devnull+user.domain@kernel.org`; the
  index uses its `X-Original-From`, so `f:david@ixit.cz` works.

## Recipes

```sh
lore search -t 'dfn:arch/arm64/boot/dts/qcom/sm7225-* is:patch'      # prior art for a SoC/device
lore search -t 'b:"samsung,s6e3fc2x01" OR dfn:panel-samsung-s6e3fc2x01*'  # has anyone upstreamed this part?
lore search 'f:andersson@kernel.org is:reply b:"regulator-allow-set-load"'  # what a maintainer said about X
lore search -C 'dfb:"qcom,pmi8998-charger" d:1y..'                    # code search in patches
lore series 'https://lore.kernel.org/r/<cover-mid>'                   # all revisions of a series
lore thread --full --skip-patches <mid>                               # review discussion only
lore search -t 's:"<title words>" b:"applied"'                        # was it picked up?
lore mbox -t <mid> | b4 am -m - -o /tmp/series <mid>                  # apply with b4 (trailers, DKIM)
```

## Freshness, gaps and sandboxes

- For replies newer than the last sync (for example minutes after posting),
  run `lore sync linux-arm-msm phone-devel`. Sync needs network access and
  writes to `~/lore`; in a sandbox, request escalation or read the index as it
  is. Reading needs only read access to `~/lore`.
- A Message-ID that isn't indexed is either newer than the last sync or on a
  list that isn't mirrored. `lore add <list>` mirrors a list (small lists take
  seconds, large ones minutes). For a one-off, `b4 mbox -o - <mid>` still
  works: lore allows b4's thread downloads but blocks its search queries.
- `lore sync` checks lore's manifest and fetches only changed epochs; don't
  add polling loops against lore.kernel.org.

## Raw access

`~/lore/<list>/git/<N>.git` are public-inbox v2 epochs: one commit per
message, sender as author, subject as the commit subject, the raw message in
blob `m`. `git -C ~/lore/linux-arm-msm/git/1.git log -i --grep=REGEX
--format='%as %an %s'` greps subjects by regex. `~/lore/index.sqlite3` has
tables `msg` (one row per Message-ID: date, author, subject, irt, tid thread
root, kind p/c/r/g/o, location), `ml` (list membership) and contentless `fts`;
open it read-only with `sqlite3 'file:/var/home/sam/lore/index.sqlite3?mode=ro'`.

## Setup

`lore-sync.service` and `lore-sync.timer` beside this file are installed as
copies in `~/.config/systemd/user` and enabled with `systemctl --user enable
--now lore-sync.timer`; `journalctl --user -u lore-sync` has the sync logs.
`~/.local/bin/lore` links to `lore.py`. This directory is linked into
`~/.agents/skills` (Codex and OpenCode read it) and `~/.claude/skills` (Claude
Code); all three follow the symlinks.

Status (2026-09-23): the 20 lists in `~/lore/lists` are mirrored and indexed:
3.23 million unique messages, about 17 GB of git plus a 5.2 GB index. The first
clone took 23 minutes and the first index about 12; an incremental sync takes
about 5 seconds. Search, thread, show, series and `lore mbox -t | b4 am -m -`
were checked on real threads, and reads work inside Codex's read-only sandbox.
Claude Code, Codex and OpenCode all list this skill.
