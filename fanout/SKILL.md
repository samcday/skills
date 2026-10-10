---
name: fanout
description: Run a task as parallel Delta subagent waves (scouts, workers, reviewers) with file partitioning, short reports, and a decision gate. Use when the user asks to swarm, fan out, go brr, or review/research/implement something thoroughly with many agents.
---

# fanout

Before writing the brief, verify and state as facts: subagent profiles and
models (`delta_settings get /subagents`), concurrency caps
(`/subagent_concurrency`), worktree state, and anything the user claimed
that you can check. Mark unverified claims as such in the brief.

Structure the brief as waves. Spawn each wave all at once.

1. Scouts: read-only, shared worktree, one question each, "no mutating
   commands". Partition by question.
2. Decision gate: the parent decides from scout evidence. Ask the user only
   if no option is viable.
3. Workers: isolated, one file each. Never two workers on one file.
   Each runs its own check before returning.
4. Reviewers: a different model than the workers. One concern each.
   "Blockers only."
5. One worker applies blockers. Parent writes the final report.

Every subagent task ends with: "Final report: at most 300 words, findings
only, no narration." Reports land in the parent context; this is the cost
that scales.

On a provider 429, stop the wave and report counts. Do not retry in a loop.

Final report: what changed, what each wave verified, what is unverified,
and per wave spawned / succeeded / failed / 429.
