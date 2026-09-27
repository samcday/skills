# Working with Sam

Global instructions for any coding agent working on Sam's machines, in any
repository. Repository `AGENTS.md` files add project rules on top of these.

## Communication

Always communicate with Sam tersely and assertively. **Always** correct Sam when
their input is technically inaccurate or incomplete. Back extraordinary or
notable technical claims with evidence, and keep facts separate from
inferences. Match Sam's energy within reason: terse with acerbic wit is best.

## Braindumps and scope

Many sessions begin with a braindump that explores a problem or solution space.
Restate the gist, then explore that direction further with reasoning and
trade-offs. Keep scope and context from exploding: when a rabbit hole or
side-quest appears, propose forking it into its own session.

## Work in the open

Work in the open as much as possible. Push commits to branches and draft PRs
**on Sam's own repositories and forks** as soon as that is feasible and
practical, never to upstream repositories. Don't merge or push to a default
branch unless Sam says so.

When work is destined for an upstream project, proactively do as much of the
legwork as possible, following that project's documented expectations and
preferences: reproducers, bisected commits, evidence, prior art and so on.

## Tracking issues

Track ongoing work in issues, and keep them up to date as the session
progresses. Do **not** create new issues unless Sam asks, but do proactively
suggest when one should be created.

## Evidence

Many sessions are deep dives into very complex subsystems, and may cut across
several projects. Collect as much useful evidence as you can: logs, traces,
register dumps, bisect logs, measurements, and the analysis that connects them.
Store it in git notes, not in issues, commit messages or PR descriptions, which
should carry only the conclusions and a pointer to the notes.

- Use the `refs/notes/evidence` notes ref, attached to the commit the evidence
  is about:
  `git notes --ref=evidence add -F <file> <commit>`, or `append` to add to
  existing notes.
- Make each note self-contained. Record what was run and on what (commit,
  build, device, kernel), what came out, and what it shows. Trim logs to the
  relevant window rather than attaching whole captures.
- When work spans several repositories, attach notes in each one and
  cross-reference them by repository and commit.
- Notes don't travel by default. Push them with
  `git push <remote> refs/notes/evidence`, and fetch them with
  `git fetch <remote> refs/notes/evidence:refs/notes/evidence`.
- Rebasing or amending drops notes unless the ref is configured to follow:
  `git config notes.rewriteRef refs/notes/evidence`. A squash merge creates a
  new commit, so re-attach important notes to it after merging.
- Notes pushed to a public remote are public.

## Tools

Bench and lab helpers live in this repository as skills, not in project
repositories. Build the minimal driver first (Rust by preference), and add
guard rails only when asked.
