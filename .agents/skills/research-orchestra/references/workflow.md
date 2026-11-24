# Workflow and handoffs

Use a run directory distinct from the source materials. The Python commands work through Python 3.10 or later. Examples use `python` and project-relative paths from the repository root.

```text
python .agents/skills/research-orchestra/scripts/run.py init .smpl-runs/paper --mode writing --template starter path/to/notes.md path/to/results.csv
python .agents/skills/research-orchestra/scripts/run.py status .smpl-runs/paper
python .agents/skills/research-orchestra/scripts/run.py mark .smpl-runs/paper outline
python .agents/skills/research-orchestra/scripts/tex.py build .smpl-runs/paper
python .agents/skills/research-orchestra/scripts/run.py refresh .smpl-runs/paper
```

The coordinator alone updates checkpoints with `mark` after inspecting a stage's output. Add `--encoding cp1252` at initialization for Windows-1252 text. The default accepts UTF-8 with or without a BOM. Other supplied files are copied byte-for-byte. Each material needs a distinct basename. The initial paths in the run manifest are local and are used by `refresh` to copy changed inputs. Run `refresh` and then `status` before resuming. Keep the manifest and work directory with the project.

| Stage | Uses | Produces inside the run directory |
|---|---|---|
| Plan | Research question and any supplied resources | `protocol.md` and `evidence-checklist.md` |
| Outline | Materials and venue | `outline.md` and `claims.json` |
| Literature | Outline, publications, and any supplied citation corpus | `literature.json` and `references.bib` |
| Figures | Outline and measured data | `figures.json`, figure files in `figures/`, and any source scripts or data references |
| Draft | Outline, claims, literature, and figures | `title.tex` and `body.tex` |
| Review | Draft, supporting evidence, and venue | `review.json`, plus earlier round reports under `reviews/` |
| Build | Reviewed draft and selected template | `paper.pdf` and `build.log` |

Write JSON as UTF-8. Example records below describe the minimum contracts. The helper validates evidence file presence, stage freshness, and the use of verified bibliography keys. It does not judge scientific validity or whether a paper supports a statement.

```json
{"claims":[{"id":"C1","kind":"measured","statement":"Condition A had a score of 3 on this fixture.","evidence":[{"file":"results.csv","locator":"row A, score column"}]}]}
```

Claim kinds are `measured`, `literature`, `interpretation`, and `hypothesis`. Give measured claims an input material and locator. Literature claims may point to a verified citation by adding a `key` and `locator` in their evidence item. Describe interpretation as such and trace it to its basis. Exclude unsupported numbers, authorship, and results. The record may include tentative claims, but only supported claims belong in the manuscript as facts.

```json
{"sources":[{"key":"example","url":"https://example.org/paper","locator":"Section 2, paragraph 1","supports":"Description of the practice supported by the source.","verified":true}]}
```

Verify records against primary sources and write matching entries to `references.bib`. Log unverified candidates in the literature record with `verified: false`. Exclude their keys from the bibliography and manuscript. Use standard LaTeX citations such as `\cite{key}`, `\citet{key}`, and `\citep{key}`. An empty bibliography and sources list are valid if there are no verified references.

```json
{"figures":[{"path":"figures/result.pdf","source":"materials/results.csv","caption":"Measured values by condition."}]}
```

The figures role should keep a reproducible source or the supplied original and use a caption that describes the measurement, units, and uncertainty if available. Use records with paths relative to the run. For a run with no figures, use an empty array. Put the title alone in `title.tex` and sections, abstract, figures, and tables in `body.tex`. Leave the preamble, title command, and bibliography rendering to the profile. Copy editable source and image files into the delivered source package.

```json
{"decision":"revise","findings":[{"severity":"major","location":"Results, paragraph 1","issue":"The cited measurements cover only one dataset.","resolution":"Narrow the claim or supply results from additional datasets."}]}
```

Use `approve` for vetted drafts, `revise` for fixable issues, and `blocked` when the current evidence cannot support the requested paper. An approved review may record non-blocking limitations. Keep prior reports with unique names before writing a revised report. Only the final approved review enables a build.

## Role briefs

- **Coordinator:** Convert evidence into a scoped contribution and a section-level outline. Capture the provenance of every table and headline number. Give workers explicit input files and distinct output files, and validate each handoff before proceeding.
- **Literature:** Look for original work behind baselines and close alternatives to the contribution. Use more than one discovery route when the literature claim calls for it. Check the source itself and record a locator and supported statement. Check claims of novelty against closely related work.
- **Figures:** Plot verified numbers with labels and units. Prefer an editable plot script for a derived chart. Use an explicit diagram for conceptual relationships. Mark illustrative figures accordingly.
- **Writer:** Use a claim, method, evidence, and limitation structure appropriate to the venue. State baselines and evaluation setup. Use the same values, units, and conditions in prose, tables, and figures. Flag uncertain claims in the draft and tell the coordinator what evidence would settle them.
- **Reviewer:** Compare claims with source rows, calculations, and cited passages. Check design, baselines, uncertainty, limitations, and unwarranted causal or novelty claims. Provide a location, concrete issue, and actionable resolution. A mismatched central result or unsupported central claim blocks approval.
- **Production:** Compile the accepted sources, read the build log, and inspect the PDF for missing glyphs, unresolved references, misplaced graphics, and template requirements. Treat the selected template as formatting guidance. Submission suitability requires venue and researcher checks.
