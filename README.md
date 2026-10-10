# skills

Sam's global agent instructions and skills, shared by every harness (Claude
Code, Codex, Delta, OpenCode, ZCode). `AGENTS.md` holds the global instructions; each
other directory is one skill (`SKILL.md` plus any helpers).

- `masoncl-prompts-land`: land changes in `samcday/review-prompts` when explicitly requested.
- `lore`: search Linux kernel mailing lists through lei and local public-inbox mirrors.
- `fanout`: run a task as parallel Delta subagent waves (scouts, workers, reviewers).

## Install

Skills and global instructions are installed separately.

### Skills

Skills are installed with the [`skills` CLI](https://github.com/vercel-labs/skills),
the same way as any third-party skill:

    npx skills add samcday/skills -g
    npx skills update -g      # pull newer versions later

That copies each skill into `~/.agents/skills/<skill>` (the canonical copy for
Codex, Delta, OpenCode, ZCode and others that read that directory) and symlinks
it into the harnesses with their own skill directory, such as
`~/.claude/skills/<skill>` for Claude Code. `~/.agents/.skill-lock.json`
records what is installed and from where.

Do not clone this repository to `~/.agents/skills`. That directory belongs to
the `skills` CLI, and anything else that lands in it is treated as a skill.
In particular Delta scans it recursively, so a stray checkout, worktree or
backup copy that contains another `lore/SKILL.md` makes `lore` a duplicate
name, and Delta then drops the skill entirely (`skipping duplicate global
skill` in `delta.log`).

To work on a skill, clone this repository anywhere else and install from the
checkout (`npx skills add ./path/to/skills -g`), or edit the installed copy and
copy the change back.

### Global instructions

`install` links `AGENTS.md` into each harness's global-instructions path and
leaves anything already in the way for you to move aside. Run it from a clone
kept anywhere; the links point at that clone, so keep it.

    git clone https://github.com/samcday/skills ~/src/skills
    ~/src/skills/install

| Harness | Global instructions | User skills |
|---|---|---|
| Claude Code | `~/.claude/CLAUDE.md` (linked) | `~/.claude/skills/<skill>` (symlinked by `skills`) |
| Codex | `~/.codex/AGENTS.md` (linked) | `~/.agents/skills` |
| Delta | `~/.config/delta/AGENTS.md` (linked; overrides below) | `~/.agents/skills` |
| OpenCode | falls back to `~/.claude/CLAUDE.md`, unless Claude Code compatibility is disabled | `~/.agents/skills` |
| ZCode | `~/.zcode/AGENTS.md` (linked) | `~/.agents/skills` |

Delta's personal rules directory is `~/.config/delta` on Linux and macOS.
On Linux, `XDG_CONFIG_HOME` replaces `~/.config`; `DELTA_CONFIG_DIR` overrides
the whole directory on either platform. Run the installer with the same
environment as Delta. Delta loads a non-empty `AGENT.md` before `AGENTS.md`,
so an existing `AGENT.md` can shadow this link. See [Delta's rules
documentation](https://delta.dev/docs/configuration/settings#rules).

Delta discovers [personal skills](https://delta.dev/docs/agents/skills)
directly in `~/.agents/skills`, including nested category folders. Install on
each machine where Delta runs.
