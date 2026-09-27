---
name: memory-curation
description: Pair with Sam to comb through agents' private memory stores and turn durable knowledge into AGENTS.md files, docs, skills or tracking-issue updates. Use when Sam asks to curate, collate, organise or flush memories, or when a memory index is near its size limit.
---

# Memory curation

Agents keep private memories that only one harness on one machine can read.
This pass periodically finds what is worth keeping for good and puts it
somewhere every agent and machine can read. How each agent uses its own memory
is not prescribed. This is a pairing session: you propose, Sam approves in
batches.

## Where things go

| Memory is about | Destination |
|---|---|
| How Sam works | `AGENTS.md` in this repository |
| One repository's conventions, gotchas or design rationale | That repository's `AGENTS.md` if it fits in a line, otherwise a doc it links to |
| A repeatable procedure or tool | A skill in this repository |
| The state of an effort | Its tracking issue |
| Private reference (device identifiers, addresses, personal details, third parties) | Sam's private repository, never a public one |
| Anything else | Leave it alone |

## Process

1. List the memories added or changed since the last pass, in every store on the
   machine, not just the current project's. Known stores:
   - Claude Code: `~/.claude/projects/*/memory/`, one per project.
   - Codex: `~/.codex/memories/`.
   - Check for others when a harness is added.
2. Work in batches of about ten, grouped by kind. For each memory, propose
   either a destination with the exact text, or leaving it alone. Quote what
   is being dropped.
3. Apply the batch Sam approves:
   - Repository changes go through draft PRs in the destination repositories.
     Once the PR merges, trim or delete the memory. Until then, add a line to
     the memory pointing at the PR.
   - Issue updates are posted directly. Once posted, trim or delete the memory.
4. Rebuild the harness's memory index so each entry is one short line. Claude
   Code stops loading `MEMORY.md` after 200 lines or 25 KB.

## Rules

- Public destinations get no private material. Before pushing, grep the diff
  for addresses, serials, phone numbers, tokens and names of third parties.
- Promote the rule, not the story. Dates, thread IDs and anecdotes stay behind
  unless they explain why the rule exists.
- `AGENTS.md` files load in every session. Keep them short and principle-level,
  and move procedures into skills or docs.
- Drop anything a session could rediscover by reading the code.
