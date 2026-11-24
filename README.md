# SMPL Papers

SMPL Papers is a Codex skill for planning experiments and turning research evidence into reviewed LaTeX manuscripts and PDFs. It connects manuscript claims to the measurements or published sources that support them and preserves the intermediate files so a run can be inspected or resumed.

## Quick start

1. Clone `smpl-works-hq/SMPL-Papers` and open the clone in Codex. The project skill is in `.agents/skills/smpl-papers/`. To use it in another project, copy that skill folder into the target project's `.agents/skills/` and open the project in Codex.
2. Install Python 3.10 or later for the checkpoint and build helpers. PDF production also needs `pdflatex`, `bibtex`, and the usual LaTeX packages for the selected profile. On Windows, the build helper can use native TeX or WSL. On Ubuntu, the TeX Live base, recommended, extra, and recommended fonts packages cover the bundled profiles.
3. Attach your files or give Codex their paths. Invoke the skill with a goal and, for a manuscript, a template preference. Use UTF-8 text where possible. The intake helper also accepts Windows-1252 with `--encoding cp1252`.

The default writing example uses synthetic measurements and a fixed literature corpus.

> Use `$smpl-papers` to draft and review a short paper from the files in `examples/lookup/`. Use the fixed citation fixture and the starter LaTeX template.

> Use SMPL Papers to plan the experiment in `examples/planning/question.md`. Produce the protocol and evidence checklist.

Add “use Codex subagents” to the prompt if you want the parallel literature and figure assignments and a reviewer with fresh context. A single assistant can carry out all stages. Codex handles the reasoning and writing in the active chat. The Python helpers run locally and need only the standard library. Figure generation may use plotting tools available in your environment.

## What each mode does

| Mode | Inputs | Outputs |
| --- | --- | --- |
| Writing | Research notes, existing experiment results, data and figures, an optional citation corpus, and a template choice | Outline, claim and citation records, figures with sources, LaTeX, review reports, PDF, and build log |
| Research planning | A research question, available resources, and constraints | `protocol.md` and `evidence-checklist.md` with hypotheses, controls, baselines, metrics, and a decision rule |

Research planning produces a protocol for the researcher to execute. Writing draws numerical claims from supplied evidence and flags gaps and limitations.

## Manuscript workflow

1. **Coordinator.** Inventories source files, records the basis for each substantive claim, and prepares an outline for the selected venue.
2. **Literature and figures.** The literature role verifies source identity and support for the cited statement. It saves a bibliography and a record of the supporting passages. The figure role works from supplied measurements, recording the data source and any script used to generate each figure.
3. **Writer.** Drafts the methods, results, and argument using the outline and verified evidence. The manuscript should agree with the measurements in the tables and figures.
4. **Reviewer.** Checks scope, data, claims, citations, experimental design, and limitations. The writer can make up to two revisions. Significant unresolved findings block the build and remain visible in the review report.
5. **Production.** Compiles the approved version with LaTeX and BibTeX. It checks references and verifies that the build produced a fresh PDF. Codex inspects the PDF and reports any remaining issues.

## Files and resumption

Each run defaults to `.smpl-runs/<name>/`. The run manifest and source snapshots stay in that directory. Stage results include `claims.json`, `literature.json`, `figures.json`, `review.json`, and, for a successful build, `paper.pdf` and `latex-source/`. Keep the rest of the run directory for figures that use reusable scripts and source measurements.

To resume, ask Codex to continue a named run. The coordinator refreshes changed source files, checks status, and resumes from the earliest stale or missing checkpoint. A changed draft invalidates the prior approval. A failed rebuild leaves its error in `build.log` and retains the previously generated PDF as `paper.previous.pdf`.

```sh
python .agents/skills/smpl-papers/scripts/run.py refresh .smpl-runs/my-paper
python .agents/skills/smpl-papers/scripts/run.py status .smpl-runs/my-paper
python .agents/skills/smpl-papers/scripts/tex.py build .smpl-runs/my-paper
```

On Windows, add `--wsl-distro Ubuntu-22.04` to choose that specific WSL distribution, or omit it to prefer native TeX. See the skill's [workflow and artifact contracts](.agents/skills/smpl-papers/references/workflow.md) for diagnostics and file formats.

## Templates

| Profile | Use |
| --- | --- |
| `starter` | A bundled article template for drafts and general reports |
| `cvpr2025` | CVPR 2025 review styling from the official author kit |
| `iclr2025` | ICLR 2025 styling from the official author kit |

The venue profiles fetch pinned, checksum-verified archives on first use, then reuse the cache. Author kit files retain their upstream notices and terms. The template profiles use archived formatting rules. Check page limits, anonymity, and any submission requirements against the intended venue and year.

## Tests

```sh
python -m unittest discover -s tests -v
python tests/smoke_pdf.py
```

The first command checks intake, checkpoint invalidation, citations, review gating, author-kit integrity, and build failure behavior. The second compiles a small cited manuscript with each profile. For WSL, use `python tests/smoke_pdf.py --wsl-distro Ubuntu-22.04`. Use `--profiles starter` to run just the bundled template without fetching venue archives.

The initial release passed eight focused tests and compiled and visually checked a PDF for each profile. A live Codex smoke test exercised the literature, figure, writing, and independent review handoffs on the synthetic example. The reviewer flagged an unsupported generalization and approved the revision after it was removed. The planning smoke test produced a protocol and evidence checklist from a question with no results.

## License

The original project files are licensed under the [MIT License](LICENSE).
