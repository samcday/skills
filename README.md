# skills

Sam's global agent instructions and skills, shared by every harness (Claude
Code, Codex, OpenCode, ZCode). `AGENTS.md` holds the global instructions; each
other directory is one skill (`SKILL.md` plus any helpers).

- `lore`: search Linux kernel mailing lists through lei and local public-inbox mirrors.

## Install

    git clone https://github.com/samcday/skills ~/.agents/skills
    ~/.agents/skills/install

`~/.agents/skills` is where Codex, OpenCode and ZCode look for user skills.
`install` links the rest, and leaves anything already in the way for you to
move aside. Re-run it after adding or removing a skill.

| Harness | Global instructions | User skills |
|---|---|---|
| Claude Code | `~/.claude/CLAUDE.md` (linked) | `~/.claude/skills/<skill>` (linked per skill) |
| Codex | `~/.codex/AGENTS.md` (linked) | `~/.agents/skills` |
| OpenCode | falls back to `~/.claude/CLAUDE.md`, unless Claude Code compatibility is disabled | `~/.agents/skills` |
| ZCode | `~/.zcode/AGENTS.md` (linked) | `~/.agents/skills` |

Claude Code keeps its own cache of claude.ai skills in `~/.claude/skills/synced`,
which is why skills are linked one by one instead of linking the directory.
