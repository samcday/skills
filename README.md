# skills

Sam's global agent instructions and skills, shared by every harness (Claude
Code, Codex, Delta, OpenCode, ZCode). `AGENTS.md` holds the global instructions; each
other directory is one skill (`SKILL.md` plus any helpers).

- `delegate`: hand bounded work to GLM-5.3-Flash (ZCode) or DeepSeek (OpenCode).
- `lab-relay`: switch the lab USB relay and hard power-cycle the DragonBoard 410c.
- `lore`: search Linux kernel mailing lists through lei and local public-inbox mirrors.
- `memory-curation`: turn durable agent memories into `AGENTS.md`, docs, skills
  and issue updates.

## Install

    git clone https://github.com/samcday/skills ~/.agents/skills
    ~/.agents/skills/install

`~/.agents/skills` is where Codex, Delta, OpenCode and ZCode look for user skills.
`install` links the rest, and leaves anything already in the way for you to
move aside. Re-run it after adding or removing a skill.

| Harness | Global instructions | User skills |
|---|---|---|
| Claude Code | `~/.claude/CLAUDE.md` (linked) | `~/.claude/skills/<skill>` (linked per skill) |
| Codex | `~/.codex/AGENTS.md` (linked) | `~/.agents/skills` |
| Delta | `~/.config/delta/AGENTS.md` (linked; overrides below) | `~/.agents/skills` |
| OpenCode | falls back to `~/.claude/CLAUDE.md`, unless Claude Code compatibility is disabled | `~/.agents/skills` |
| ZCode | `~/.zcode/AGENTS.md` (linked) | `~/.agents/skills` |

Claude Code keeps its own cache of claude.ai skills in `~/.claude/skills/synced`,
which is why skills are linked one by one instead of linking the directory.

Delta's personal rules directory is `~/.config/delta` on Linux and macOS.
On Linux, `XDG_CONFIG_HOME` replaces `~/.config`; `DELTA_CONFIG_DIR` overrides
the whole directory on either platform. Run the installer with the same
environment as Delta. Delta loads a non-empty `AGENT.md` before `AGENTS.md`,
so an existing `AGENT.md` can shadow this link. See [Delta's rules
documentation](https://delta.dev/docs/configuration/settings#rules).

Delta discovers [personal skills](https://delta.dev/docs/agents/skills)
directly in `~/.agents/skills`, including nested category folders; no extra
skill links are needed. Install on each machine where Delta runs.
