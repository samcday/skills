---
name: masoncl-prompts-land
description: >-
  Land changes in samcday/review-prompts only when the user explicitly requests
  landing or merging. Do not invoke for review, preparation, passing checks,
  or skill installation. This workflow does not apply to other repositories.
disable-model-invocation: true
metadata:
  delta-action: land
---

# Land review-prompts

An explicit invocation authorizes this workflow. Do not ask again whether to land.
Publish only to `samcday/review-prompts`, never to `masoncl/review-prompts`.
Use Git and `gh`. Run Git operations non-interactively with `GIT_EDITOR=true`.

## Workflow

1. Work from the repository root. Source references below are relative to that root.
   Inspect the current repository, changes, and remotes.
   Confirm that `origin` identifies `samcday/review-prompts`. The destination is `main`.
   Never publish through the `local` remote.
   Identify the requested changes, including uncommitted files.
   Preserve unrelated files, staged changes, and commits. Stop if the scope is unclear.

2. Fetch the fork's current `main` and create small, descriptive commits for the requested changes.
   Rebase only unpublished work onto the current fork history.
   For an upstream sync, fetch `main` from `https://github.com/masoncl/review-prompts.git` and merge it into the fork.
   Preserve the upstream prompts and keep fork-specific packaging changes separate where practical.
   Resolve clear conflicts automatically. Stop when the intended result is unclear.
   Do not discard unrelated work, force-push, or rewrite published history without separate explicit permission.
   Do not open upstream pull requests.

3. Run `git diff --check` and `python3 tests/skills.py` against the final proposed tree.
   The test command is defined by `main()` and the script entry point in `tests/skills.py`.
   That script owns the isolated installation tests. Do not substitute tests against personal skills.
   It requires Git, Bash, Python 3, Node.js 22.20.0 or newer, npm, and registry access.
   Inspect current destination rules with `gh` and satisfy all applicable reviews and required checks.
   Every required check must pass for the exact changes being landed.
   Pending, failing, missing, or unverifiable required checks block landing.
   Do not bypass branch rules or change permissions. Stop if the permitted landing route is unavailable.

4. Push the intended commits to the fork's `main` with a normal push.
   If the destination advances, integrate those commits and repeat verification before retrying.
   Confirm through a fresh remote read that the destination contains the landed commits.
   A local commit or a published topic branch alone is not a completed landing.

5. Confirm public skill discovery from `samcday/review-prompts` after publication.
   Reuse the pinned CLI, `--list` invocation, and temporary environment from `tests/skills.py`.
   Replace only the test source with the GitHub repository for this discovery run.
   Do not install into personal skill directories.
   Report the landed commit and the checks performed. Packaging tests do not prove a live review workflow.
   If blocked before publication, state that the changes did not land.
   If publication succeeds but discovery fails, report both facts without claiming complete success.
