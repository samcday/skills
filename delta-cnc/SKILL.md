---
name: delta-cnc
description: "Wrangle ongoing Delta threads in-session: command-and-control triage, compare desktop/laptop catalogs, recheck pruning, find the next bounded action, and prepare threads for archival. Use for cross-machine thread inventories or a closeout queue, not ordinary single-thread lookup. Cheap metadata first; selected history only."
---

# Delta CnC

Keep one authoritative working thread per task. Close finished side discussions
where they live; import only work worth continuing. This is an operating procedure
over Delta's CLI, not a second thread database or an autonomous cleanup agent.

## Start cheap

1. **Identify the execution host every turn.** Local does not mean desktop.
   Run `hostname`; use explicit SSH targets for cross-machine work.
2. Prefer Delta's built-in `delta-threads` tool/skill for ordinary discovery and
   selected transcript reads when available. Otherwise use `delta thread search
   --scope metadata -n 10 KEYWORDS --json`. Search before listing everything.
3. For inventory/recheck work, use `cnc.py` beside this file. It needs Python 3's
   standard library (Python 3.8+), SSH for remote reads, and a current Delta CLI on each source.
   Only the source needs Delta; only the controller needs this skill checkout.
4. Inspect the installed CLI's `--help` when a capability is uncertain. Current
   syntax is `delta thread …` and `delta open …`, not the older `delta cli …`.
   CLI version labels have disagreed with the GUI; capability checks win.

The helper does **not** change Delta state. `snapshot` uses `auth status` and
`thread list`; `parents` uses `auth status` and one `thread data` per explicit ID.
It never opens threads, queries a model, syncs, forks, archives, deletes, or writes
Delta's database. Authentication failures do not trigger login.

## Snapshot, then diff

Resolve `SKILL_DIR` to the directory containing this file. Keep snapshots in a
private, machine-local cache **outside Git**, not in a public repository or a
git note. They contain titles, summaries, account IDs, and possibly join handles.
Do not commit real thread data as test fixtures.

```sh
set -eu
: "${SKILL_DIR:?set the directory containing this skill}"
: "${CURRENT_ID:?set the current thread's known opaque ID}"
STATE="${XDG_CACHE_HOME:-$HOME/.cache}/delta-cnc"
umask 077
mkdir -p "$STATE"

# CURRENT_ID must be the current thread's known opaque ID, not its share ID.
# Use the SAME exclusions on every host and on subsequent refreshes.
for host in desktop laptop; do
  python3 "$SKILL_DIR/cnc.py" snapshot --ssh "sam-$host" \
    --exclude "$CURRENT_ID" > "$STATE/$host.next.json"
  if [ -f "$STATE/$host.json" ]; then
    python3 "$SKILL_DIR/cnc.py" diff \
      "$STATE/$host.json" "$STATE/$host.next.json"
  fi
done

python3 "$SKILL_DIR/cnc.py" compare \
  "$STATE/desktop.next.json" "$STATE/laptop.next.json"

# Reached only after both captures and comparisons succeeded.
for host in desktop laptop; do
  mv "$STATE/$host.next.json" "$STATE/$host.json"
done
```

Only promote a `.next.json` file to the baseline **after its snapshot command
succeeds**. Keep the previous baseline if collecting fails. Do not take an empty
or failed output as an empty catalog. Scratch files disappear between agent turns;
they are fine for a one-off audit but not a recurring baseline.

The helper records the **observed hostname and account**, not just the SSH alias.
`compare` requires different observed hostnames and the same account. `diff`
requires the same hostname, account, and CLI path. Exclusions must match.
If the current thread ID is unknown, do not invent it: document that current-thread
filtering may differ between an in-app local command and a remote CLI command.

The collector runs over SSH stdin and reduces results **on the source machine**.
Nothing is installed remotely. CLI lookup uses PATH, then
`~/.local/delta.app/bin/delta`; `--delta PATH` overrides it on that source.
This helper targets the CLI's default data directory/account. For alternate
channels or data directories, use native CLI selectors after checking their help;
do not silently mix stores or scrape an assumed SQLite path.

## Interpret the inventory correctly

Keep these questions separate:

| Question | Evidence |
|---|---|
| Is it in this local catalog? | Snapshot membership |
| Is its catalog row archived? | `catalog_archived` |
| Is a child archived in its parent? | A fresh, selected parent inspection |
| Has its conversation caught up? | Selected history/version comparison, not metadata |
| Is the work complete? | Final request, result, landing/handoff receipt, remaining decisions |

- Missing locally does not mean absent from the server. A missing child may still
  be discoverable through its shared parent.
- A missing `join_id` does not prove the thread was never uploaded.
- `catalog_archived=false` is **not** a reliable count of open jobs. A parent's
  child entry can already be archived while the child's catalog row says false.
  Conversely, an archived parent does not prove all child tasks are complete.
- Stable metadata is not a transcript revision. Different activity timestamps,
  generated titles, or runtime counters do not establish conflicting edits.
- Zero unpushed deltas is not proof of equal conversations, equal worktrees, or a
  recoverable server copy. Label catalog, conversation, and checkout audits separately.

Do not repeat a whole-history audit for a routine pruning refresh. Diff catalogs,
select the few changed or decision-relevant parents, then stop.

## Selected parent state: the expensive escape hatch

```sh
python3 "$SKILL_DIR/cnc.py" parents --ssh sam-laptop PARENT_ID
```

This emits child names, opaque native references, kinds, and
`archived_in_parent`. It intentionally does not reverse-engineer thread IDs from
MessagePack or join by title. Discover a child's actual ID with metadata search;
duplicate names require more context.

`thread data` still materializes and serializes the **entire thread** on the source.
Filtering its output saves network traffic and model context, not the source work.
Each report includes CLI-call count, source JSON bytes, elapsed time, and (over SSH)
returned bytes. These are not LLM token or billing measurements.

For a routine refresh, start with at most **three explicitly selected parents**.
Expand only for a concrete unanswered question. Batch IDs on one source to share
the authentication probe. Reuse that result within the turn instead of reloading it.
Do not routinely launch scouts to reread the same shortlist.

A failed parent read is reported as `error`, **not an empty child list**. `parents`
exits 1 if a selected read or parent projection fails; setup and inventory-schema
errors exit 2. A persistent
“history … was reset while its state was awaited” error leaves the history
unverified. Missing archived local history can be ordinary eviction. Do not
retry indefinitely, reset history, or infer corruption. Ask before opening/syncing
anything to repair an audit.

## Read enough to close the task, not its ancestors

For a shortlisted thread, use cached summaries to orient, then inspect the latest
relevant original user/assistant messages. `show --format summarize` can omit
context needed for a closeout. Numbered subthread ranges have also returned
inherited parent text; verify that the text belongs to the child's own request.
Use a bounded full-transcript read when needed, or mark the conclusion unknown.
Do not declare an unfinished inherited parent task to be an unfinished child task.

When using the native CLI:

```sh
delta thread status THREAD_ID --json
delta thread show THREAD_ID --format messages --messages FIRST-LAST --json
```

Message ranges are inclusive, at most 20 messages, and their positions are not
durable IDs. Use ranges learned from the selected transcript, not guessed offsets.
`thread query` makes a model request; it is not a cheap metadata lookup.

## Closeout board

Return a small queue, with one next action and exit condition per retained thread:

- **Already archived:** exclude from fresh cleanup wins, even if the catalog differs.
- **Archive-ready:** the requested scope is done, findings are handed off, and any
  landing/parent reconciliation is acknowledged.
- **One closing action:** specify exactly what is missing—draft disposition,
  receipt, review, preserved finding, or explicit preserve/retire decision.
- **Blocked:** name the owner and trigger. Do not keep a vague “ongoing” bucket.
- **Keep active:** one bounded next slice, with authorization gates intact.
- **Unknown:** unreadable history or insufficient evidence; do not turn it green.

Check unsent drafts in the UI before archival. This helper does not expose them;
**unknown is not “no drafts.”** Also distinguish unsent prompts from unmerged PRs
and uncommitted code. Approval is not a merge receipt. Archival is not permission
to delete checkouts, branches, recovery stashes, or evidence.

Do not wake task agents just to ask for status. Sending an agent message can start
work and spend tokens. Dispatch a bounded closing action only when requested;
do not let a thread inventory authorize hardware operations, deployment, or spending.

## Bring over an explicitly requested thread

Find it on the source, use its existing `join_id` from the catalog, then:

```sh
delta open 'https://delta.dev/join/EXISTING_JOIN_ID'
```

Open that link **on the requested destination**, not whichever machine runs this
turn. A zero exit code only means the open request was accepted. Verify local
discovery and selected conversation availability afterward.

Use the original thread, not a fork. A `delta://thread/ID` link is for an already
local thread; prefer the HTTPS join link for transfer (the custom join scheme has
failed silently in this workflow). Do not widen sharing permissions. If no join
handle is available, inspect the parent/share UI rather than manufacturing a link.

Archive through a verified supported action when the user requests it. The tested
CLI has no archive subcommand; never substitute `delete`, `discard`, or raw SQL.
`thread sync` writes state and has required the GUI to be closed in some builds:
check the installed help and do not use it as a read or casually restart Delta.
Never enable a hidden sync setting merely because its config key exists.

## Report and evolve

Lead with changes since the previous snapshot, remaining disagreements, and the
next small closeout batch. State coverage and unknowns. Separate measured CLI/data
cost from unmeasured model/orchestration cost.

The [roadmap](https://delta.dev/roadmap) lists CLI work in progress; it promises no
particular shipped command or schema. When Delta gains compact parent/archive
projections or revision-based queries, replace the expensive read here. Do not
grow this helper into a competing Delta database, scheduler, or UI automation layer.
