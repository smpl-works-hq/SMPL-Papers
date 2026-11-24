---
name: smpl-papers
description: Plan and run bounded research experiments or turn supplied research evidence into a reviewed LaTeX manuscript and PDF. Use for protocols, measured trials, scholarly lookup, reproducible figures, and manuscript production.
---

# SMPL Papers

Choose **planning** for a protocol, **experiment** when the user asks to execute trials, and **writing** for a paper supported by existing results. Treat supplied files as sources. Store new runs under `.smpl-runs/` in the current project unless the user selects another destination. Read [the workflow](references/workflow.md) for the helper commands and file contracts. Use `run.py next RUN` to get the ready assignments, their prompts and the next helper on a new or resumed run.

## Research

Write the prediction, baselines, controls, metric and decision rule to `protocol.md` and the evidence to collect to `evidence-checklist.md`. Mark the plan. Planning delivers these two files.

In experiment mode use `run.py brief RUN experiment --specialty evaluation` or select training or tracking as appropriate. Follow the [experiment procedure](references/workflow.md#experiments) to register the command, execute it and record measured outcomes. Reflect on failures and inconclusive results. Carry out the next justified trial within the user's scope and budget. To write from an experiment, give the writing run its registered protocol, raw measurements, findings and trial summary.

## Manuscript

1. Use `run.py brief RUN outline`. Write section assignments, claim records, and figure specifications. Mark the outline and run `run.py prepare RUN` to dispatch scholarly discovery and figure rendering concurrently. Select fixture mode when the user supplies a fixed corpus for an offline run.
2. Expand the `literature` and `figures` briefs. When the user authorizes Codex subagents, send these briefs together with the run path and give each agent its own outputs. Otherwise complete both assignments in this chat. The coordinator waits for and marks both checkpoints. Apply the installed specialist instructions selected by each brief.
3. Use the `writer` brief to draft from the verified sources, section synthesis, figures and measurements. Mark the draft and run `tex.py preview RUN` to compile it and render every page.
4. Expand the `methods`, `evidence`, `presentation` and `layout` briefs for this preview. Use independent readers when authorized or separate passes within the active chat. Give each reader the artifacts and its rubric without the writer's self-assessment or other readers' results. Each returns a score, findings, decision and the exact PDF hash. The layout reader opens every page image.
5. Run `reviews.py RUN`. Use the `revision` brief for fixable issues. Mark the affected stages, compile a new preview and obtain fresh reviews. Compare the archived findings and scores across up to three content revisions and a dedicated layout correction if needed. Report unresolved blockers when evidence or the revision budget is exhausted.
6. After approval mark `review` and run `tex.py build RUN`. Deliver the approved PDF, editable manuscript, figures, evidence and score summary. Trace numerical results to their measurements and check whether each source passage supports the manuscript's interpretation.
