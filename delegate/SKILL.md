---
name: delegate
description: Hand a bounded coding or research task to a cheaper model worker (GLM-5.3-Flash through ZCode, DeepSeek V4.1 Flash through OpenCode) from Claude Code or Codex. Use when Sam asks to delegate, offload or fan out work to GLM, ZCode, DeepSeek or OpenCode, or to check their quota.
---

# Delegating to cheaper models

## Discipline

- Delegate for parallelism or context isolation, not by reflex.
- Exactly one writer per checkout. Before dispatching a follow-up, confirm the
  previous worker has finished; inspect an uncertain run instead of relaunching.
- Give each worker one bounded brief with exact paths, acceptance criteria and
  a report path. Verify its diffs, commits and test logs yourself; worker
  summaries overstate.
- Answer permission requests one concrete request at a time. Never grant yolo,
  bypass or blanket modes.
- On a usage limit, record the reset time, snapshot the work, say so once and
  wait. Never enable paid credits or switch provider or model without Sam.
- Don't burn quota on polling loops: use bounded waits and delta-only status.
  Start fresh sessions at batch boundaries with short handovers.

Pick the worker by whichever subscription has quota. Check the worker's
provider before sending it device identifiers or private material: DeepSeek on
OpenCode Go is hosted in China, and Z.ai is a third party.

## GLM-5.3-Flash through ZCode

Model, endpoint and key come from `~/.zcode/cli/config.json` (written by
`zcode login`, `model.main = zai/glm-5.3-flash`).

- **Never** pass `--mode yolo`.
- **Never** set `ZCODE_BASE_URL`, `ZCODE_MODEL` or `ANTHROPIC_API_KEY` in a
  worker's environment. Either override bypasses ZCode's endpoint routing, so
  calls are metered on the generic lane instead of ZCode's.

### Direct CLI

Use the direct CLI for read-only analysis, or for implementation where you run
the tests yourself.

    glm-flash --cwd <absolute git checkout> --out <absolute result dir> --mode plan --prompt-file <brief.md>

`glm-flash` drops `ZCODE_BASE_URL`, `ZCODE_MODEL` and `ANTHROPIC_API_KEY` from
the worker's environment itself, so an exported value can't reroute it.

- Run it in the background and wait for it to exit; don't poll.
- `plan` is read-only.
- `edit` writes files and runs low-risk shell, but `python3` and `git commit`
  are rejected headless, so run tests and commit afterwards yourself.
- `build` rejects every write headless.
- Output goes to `<out>/result.json` (`sessionId`, `response`, `usage`). stdout
  should show `snapshot_updated mappingCount 2`, which means the call was routed
  correctly.
- `--resume sess_…` continues a session.
- The reasoning level follows the ZCode user default; the CLI has no flag for it.
- Plan mode can end with an empty `response`, with the findings left in an
  ExitPlanMode tool call. For research that must produce a file, use `edit`
  with an explicit output path, and ask for the whole report in the final
  response as well.

### agentprism-zcode MCP

Use the MCP when you need a per-run thought level or a per-request permission
gate. Use the `workflow` tool, not `repl`: `repl` answers permissions
automatically.

Call `action:"run"` with an absolute `projectDir`, `maxAgents:1`,
`concurrency:1` and `agentRetries:0`:

    export const meta = { name: "<slug>", description: "<one line>" };
    return await agent("<brief>", { model: "zcode/builtin:zai-coding-plan\\GLM-5.3-Flash",
      configOptions: { thought: "high" }, mode: "build", cwd: "<absolute dir>" });

Then:

1. Call `action:"status"` with the run ID.
2. Read each pending permission's concrete request, and answer only that one
   with the `optionId` the request advertises for allow-once (ZCode has used
   `allow_once`). Never pick an always-allow option.
3. Call `action:"result"` when the run is done.

ZCode's native default mode is yolo, so always pass `mode` (`plan` or `build`)
explicitly. Choose `thought` by difficulty: `low` for mechanical work, `high`
for ordinary implementation, `max` for hard reasoning.

### Quota

    node ~/.local/share/agentprism-zcode/current/node_modules/zcode-acp-server/dist/cli.js quota glm

## DeepSeek V4.1 Flash through OpenCode

Use DeepSeek for cheap mechanical work. Call the `opencode` MCP with
`providerID opencode-go`, `modelID deepseek-v4.1-flash` and variant `high`.

- The MCP expects a server on 127.0.0.1:4096. If `opencode_setup` reports it as
  unreachable, update OpenCode (1.18.31 when this was written; older releases
  let web pages reach the local server) and start one:

      setsid nohup opencode serve --hostname 127.0.0.1 --port 4096 &

- Always pass `directory` pointing at a git checkout. A plain scratch directory
  produces empty replies.
- Tell the worker to write only under a gitignored `out/` directory.
- The `plan` agent refuses to write files, so research that must produce a file
  needs agent `build`.
- Each call carries about 14k tokens of harness overhead, so batch work into
  fewer, larger tasks.
- Prefer `opencode_fire` plus `opencode_check` for long tasks.
