---
name: lore
description: Search and read Linux kernel mailing-list archives (LKML, lore.kernel.org) with lei, over local public-inbox mirrors of linux-arm-msm, phone-devel and other qcom/phone lists, with lore.kernel.org/all/ as a fallback for every other list. Use for prior art, review feedback, patch-series revisions, what maintainers said, applying a series with b4, or any lore link or Message-ID. lore.kernel.org's web UI blocks agents, so never WebFetch it.
---

# lore: LKML search with lei

`lore` (on PATH; the script is `lore` beside this file) is a thin wrapper
around [lei](https://public-inbox.org/lei.html) and public-inbox, the software
lore.kernel.org itself runs. `~/lore` holds one public-inbox inbox per list
named in `~/lore/lists`, each registered as a lei external, and
`https://lore.kernel.org/all/` (every list, LKML included) is registered as a
remote external that is only queried with `--remote`.

lore.kernel.org's web pages return 403 to curl and WebFetch and an Anubis
challenge to browsers. lei's remote queries and b4 still work, because they
POST for mbox results, which lore allows. Prefer the local mirrors: they are
faster and don't load kernel.org.

## Freshness

Nothing updates in the background. Run `lore update` before any search where
recent mail matters (the last few days, or replies to something just posted):
it checks lore's manifest, fetches only changed epochs and indexes them, in
about 2 s when nothing changed. `lore status` shows when it last ran. A
warning that fingerprints "may have updated since" the manifest is harmless:
the list moved on while lore was writing its manifest. It needs the
network and write access to `~/lore` (see Sandboxes).

## Commands

MID is a Message-ID with or without `<>`, or a lore.kernel.org,
lkml.kernel.org or patch.msgid.link URL.

| Command | Does |
|---|---|
| `lore q QUERY` | One line per message, newest first: date, author, subject, `<Message-ID>`. `-n N` (default 25), `-t` one line per matching thread instead (first message, message count, last activity; most recently active first), `--remote` also searches lore.kernel.org/all/, `--json` lei's JSON lines instead |
| `lore thread MID` | One line per message of MID's thread, each reply under its parent: find the review replies, then `show` them |
| `lore show MID...` | The messages as text (a `# blob:... pct:...` line precedes each). `-t` the whole thread |
| `lore mbox MID` | The whole thread as mboxrd, for b4 or `git am` |
| `lore update [LIST...]` | Fetch and index (every list in `~/lore/lists` by default) |
| `lore add LIST...` | Start mirroring more lists (see Expanding the lists) |
| `lore status` | Lists, their index state, last update |

- `thread`, `show` and `mbox` take `--remote` to fetch from lore.kernel.org,
  and `thread` takes `--json`. Options can go before or after the query; words
  after `--` are all query.
- Exit status: 1 when a search or thread matches nothing, 2 on errors
  (including a malformed query), and 2 when `show`/`mbox` can't find a MID.
- Local mirrors only hold the lists in `~/lore/lists`. A reply sent only to
  an unmirrored list is missing from `thread`, `show -t` and the `-t` counts;
  DT binding review on devicetree@ and bot reviews (sashiko-bot) are typical.
  When the review history matters, check `lore thread --remote MID` too.
- `--remote` downloads every match from lore.kernel.org whatever `-n` says
  (1-3 s, more for big result sets), so bound it with `d:` or specific terms.
- `--json`: one object per message with `m` (Message-ID), `s`, `f`/`t`/`c`
  (`[name, addr]` pairs), `dt` (date), `rt` (received), `refs` (ancestor
  Message-IDs), `blob` (`lei blob OID` prints the raw message) and `pct`
  (relevance). Empty `refs`/`c` are left out. With `-t`, each line is
  `{root: <message>, messages: N, last: <date>}`.
- `-n` counts messages after dropping cross-posted copies; a heavily
  cross-posted result can still come back short, so raise `-n`.

Search, `thread` what looks relevant, then `show` the few Message-IDs that
matter; `lore show -t` of a long patch series is large. Everything else is
plain lei: `lei q -f text|mboxrd|jsonl`, `lei lcat`, `lei blob`, `lei p2q`,
`lei rediff` (see their man pages).

## Query syntax

The query language is lore's own (Xapian): terms are ANDed; `OR`, `NOT`,
`-term`, parentheses, `"quoted phrases"` and `term*` work.

| Prefix | Field |
|---|---|
| bare word | subject, From, body (quotes too), diffs, Message-ID, attachment names; not To/Cc |
| `s:` | subject |
| `f:` `t:` `c:` `tc:` `a:` | From, To, Cc, To or Cc, any address |
| `b:` `nq:` `q:` `bs:` | body including quotes, body without quotes (what the sender wrote), quoted text only, subject+body |
| `dfn:` `dfb:` `dfa:` `dfhh:` `dfctx:` | diff file name, added lines, removed lines, hunk header (function), context |
| `m:` `mid:` | Message-ID (partial, exact) |
| `l:` | List-Id, e.g. `l:linux-arm-msm` |
| `d:` `rt:` | sent / received date: `d:2.weeks.ago..`, `d:20240101..20240701` (end exclusive, UTC), `d:last.month..` |
| `patchid:` `dfpre:` `dfpost:` | `git patch-id --stable`; pre/post-image blob OIDs from `index` lines |

- `lore q` passes the query to lei in one piece. With raw `lei q`, pipe the
  query in (`echo 'QUERY' | lei q --stdin ...`): an argv word containing
  spaces turns into one phrase, so `lei q 's:foo AND f:bar'` matches nothing.
- Anything with punctuation is a phrase: `b:"qcom,sm6115-pinctrl"`,
  `f:konradybcio@kernel.org`, `dfn:arch/arm64/boot/dts/qcom/sdm845` (which
  also matches `sdm845-*.dts`). Underscore identifiers are single terms:
  `dfb:qcom_scm_call`, so `s:hx83112` misses `himax_hx83112b`.
- `*` expands one bare term only: `dfn:sm7225*`, `s:sdm84*`. After
  punctuation it does nothing (`dfn:qcom/sdm84*` matches nothing), and a
  prefix that expands to more than 100 terms (`dfb:qcom_scm*`) is an error.
- To find mail sent to or copying someone, use `a:` or `tc:`; a bare
  address only matches From and bodies.
- There is no patch/cover/reply filter. Replies: `s:re`. Cover letters:
  `(s:0 OR s:00) -s:re` (they are `[PATCH n 0/N]`).
- `NOT` binds loosely: `s:panel NOT s:dts s:samsung` means
  `s:panel NOT (s:dts s:samsung)`. Prefer `-term`, or use parentheses.
- `-term` only excludes next to a positive term; `lore q -- -s:re` alone
  returns replies. Add a date: `lore q -- d:1.week.ago.. -s:re`.
- Spell out relative dates (`d:3.months.ago..`); `3mo` is misread.
- A From of `Name via B4 Relay <devnull+user.domain@kernel.org>` still
  matches `f:user@domain`.

## Recipes

```sh
lore q -t 'dfn:arch/arm64/boot/dts/qcom/sm7225'                   # prior art for a SoC or device, by thread
lore q 's:sm8550 (s:0 OR s:00) -s:re'                             # cover letters about a SoC
lore q 'b:"samsung,s6e3fc2x01" OR dfn:panel-samsung-s6e3fc2x01'   # has anyone upstreamed this part?
lore q 'f:andersson@kernel.org s:re nq:"regulator-allow-set-load"' # what a maintainer said about X
lore q -t 'b:"change-id: 20260315-pixel3-camera-a9989bf589ee"'    # every revision of a b4 series (then thread each)
lore q 's:sm8550 s:"bandwidth scaling" s:re nq:applied'          # was it picked up? (b4 ty replies)
lore q --remote 'a:someone@example.com d:6.months.ago..'          # lists that aren't mirrored, LKML
lore mbox MID > /tmp/series.mbox                                  # then, from the kernel tree to apply to:
b4 --offline-mode am -m /tmp/series.mbox -o /tmp/series MID
lei blob --git-dir=$HOME/src/linux/.git OID                       # rebuild a file from patches (needs the preimage)
```

b4 checks the series' `base-commit` against the git repository in the current
directory, and collects trailers only from the revision's own thread: review
tags given to an earlier revision and not carried forward are not picked up.

## Expanding the lists

`lore add NAME...` appends to `~/lore/lists`, clones, indexes and registers
the lists. Names are lore's inbox names, the `NAME` in
`https://lore.kernel.org/NAME/` (all of them are in
`https://lore.kernel.org/manifest.js.gz`). The first index of a list is the
expensive part: the compacted index is about 3.3 times the size of the
list's git, and a 1 GB list (about 300k messages, like linux-arm-msm) takes
about 15 minutes and 3.5 GB. Later updates are incremental. Add a list when
you will search it repeatedly; for one-off questions `lore q --remote` is
enough.

Likely candidates: `linux-devicetree` (binding review, 3.8 GB git),
`dri-devel` (panels, 2.5 GB), `linux-arm-kernel` (4.4 GB), `u-boot`,
`alsa-devel` (ASoC before 2024), `linux-bluetooth`, `linux-wireless`. If
`lore status` lists one as "on disk, not in lists", its git is already there
from an earlier mirror and `lore add` only fetches what is new before
indexing. LKML (`lkml`) is too large; use `--remote`.

To stop tracking a list, delete its line from `~/lore/lists` and run
`lei forget-external ~/lore/NAME`; the data stays until you delete
`~/lore/NAME`.

## Sandboxes

- **Codex**: lei talks to a background `lei-daemon` over a unix socket, and
  Codex's sandbox refuses the connection, so every `lore` and `lei` command
  must run outside it. On a configured machine `~/.codex/rules/lore.rules`
  lets `lore` run there without prompting, so prefer `lore` over raw `lei`
  (which still prompts: `lei q -o` can write anywhere). Otherwise request
  escalation. A pipeline that also runs something else (`lore mbox ... | b4
  ...`) still prompts, so write to a file first.
- **Claude Code and OpenCode**: no special handling.
- The first `lei` call starts `lei-daemon`; it stays running. After upgrading
  lei or public-inbox, run `lei daemon-kill` so the next call starts the new
  code.
- Fedora's lei is a 2023 snapshot. Raw lei sometimes prints `non-fatal error
  from PublicInbox::LeiXSearch $?=139`: a worker crashed in DBD::SQLite
  cleanup after returning every result (`lore` hides it). Don't pass `-v` to
  lei queries that include `--remote`: that hangs in this build.

## Setting up a machine

1. Install `lei` (it pulls in public-inbox and Search::Xapian), `xapian-core`
   (compaction), `jq` and `b4`. They are Fedora packages; Sam's workstation
   image (`~/src/workstation-config`) already has them.
2. Put the script on PATH: `ln -s <this directory>/lore ~/.local/bin/lore`.
   Link this directory into `~/.agents/skills` (Codex, OpenCode) and
   `~/.claude/skills` (Claude Code).
3. Run `lore update`. It copies `lists` from beside this file to
   `~/lore/lists`, then clones and indexes every list: 6.4 GB of git plus
   21 GB of index for the default 17 lists, and 20-40 minutes of indexing on
   a 32-thread desktop (up to four lists index in parallel). To skip the
   download and indexing, first copy an indexed `~/lore` from another machine
   while no update runs there (`rsync -aH other:lore/ ~/lore/`); indexes are
   relocatable, and `lore update` then only fetches what's new and registers
   the externals.
4. For Codex, create `~/.codex/rules/lore.rules`:

   ```
   prefix_rule(pattern = ["lore"], decision = "allow")
   ```

`~/lore/NAME/git/N.git` are the list's git epochs (one commit per message;
`lei blob OID` prints the raw message for a `blob` from `--json`), next to
`all.git`, the Xapian index `xap15/` and `msgmap.sqlite3`.

Status (2026-09-24): the default 17 lists are mirrored and indexed on
sam-desktop (6.4 GB git, 21 GB index); linux-arm-kernel, linux-devicetree and
dri-devel have git in `~/lore` but aren't indexed. `lore update` takes about
2 s when idle, and local searches about 0.1 s.
