# skills

Sam's global agent instructions and skills, shared by every harness (Claude
Code, Codex, OpenCode, ZCode). `AGENTS.md` holds the global instructions; each
other directory is one skill (`SKILL.md` plus any helpers).

- `lore`: search Linux kernel mailing lists through lei and local public-inbox mirrors.

## Install

    git clone https://github.com/samcday/skills ~/.agents/skills
    ~/.agents/skills/install

`~/.agents/skills` is where Codex, OpenCode and ZCode look for user skills.
`install` links the rest, and refuses to replace anything that isn't already a
symlink:

| Harness | Global instructions | User skills |
|---|---|---|
| Claude Code | `~/.claude/CLAUDE.md` (linked) | `~/.claude/skills` (linked) |
| Codex | `~/.codex/AGENTS.md` (linked) | `~/.agents/skills` |
| OpenCode | falls back to `~/.claude/CLAUDE.md` | `~/.agents/skills` |
| ZCode | `~/.zcode/AGENTS.md` (linked) | `~/.agents/skills` |

`git pull` updates every harness at once.
