# SMPL Papers

SMPL Papers is a Codex skill for planning experiments, running registered trials, and turning research evidence into reviewed LaTeX papers. Each run keeps its measurements, source passages, figures, manuscript versions and review decisions together.

## Get started

Open the cloned project in Codex, or copy `.agents/skills/smpl-papers/` into another project's `.agents/skills/` folder. The helpers use Python 3.10+. For plots, PDF reading and page rendering, install the visual dependencies in your Python environment. Manuscript compilation requires `pdflatex`, `bibtex` and the TeX packages for the chosen profile. On Windows you can run the workflow inside WSL or select a WSL TeX installation for the preview.

```sh
python -m pip install -r .agents/skills/smpl-papers/requirements-visual.txt
```

| Goal | Example request | Deliverables |
| --- | --- | --- |
| Plan | “Use SMPL Papers to design a test for `examples/planning/question.md`.” | Protocol, controls, metrics, decision rule, and evidence checklist |
| Experiment | “Run the lookup experiment in `examples/planning/` using its measurement script and registered-trial specification. Explain the outcome.” | Registration, command, logs, raw metrics, outcome, and findings |
| Manuscript | “Draft and review a short paper from `examples/lookup/` using the fixed citation fixture and starter template.” | Reviewed PDF, editable LaTeX, source records, figures, and versioned reviews |

## How a manuscript is produced

1. **Outline.** The coordinator inventories claims and assigns literature queries and figures to individual sections.
2. **Search and figures.** A local command runs scholarly discovery and figure rendering concurrently. The literature role verifies selected passages against primary HTML, text or PDF, retrieves DOI metadata and BibTeX when available, and writes a section synthesis. The figure role produces plots or diagrams, or inspects supplied graphics, and checks each against its source.
3. **Writing.** The writer uses verified citations, the section synthesis, original measurements and approved figures. Checkpoints detect changed inputs and support resumption.
4. **Review.** TeX produces a candidate PDF and an image of every page. Methods, evidence, presentation and layout readers score the same version and identify changes by location. Significant open findings block approval. Each revision gets its own PDF, images, scores and decision.
5. **Delivery.** Publication copies the exact approved PDF and its matching editable sources. The run retains previous versions and their evidence.

Codex performs the roles in one chat by default. Ask for subagents to delegate literature and figure work and obtain independent reviewers. The scheduler reads the run's checkpoints and returns ready assignments with their prompts. Role briefs automatically select an available specialist for the assignment, including training, evaluation and tracking work in experiment mode.

## Configuration

Live discovery uses Crossref and Semantic Scholar. Optionally set `S2_API_KEY` for Semantic Scholar. Select `--provider google` with `GEMINI_API_KEY` and `SMPL_SEARCH_MODEL` for Google-grounded discovery. Crossref supplies registry metadata and DOI BibTeX. Fixture mode verifies a supplied corpus offline and labels its evidence accordingly.

The `starter` article profile is bundled. `cvpr2025` and `iclr2025` download and checksum their official author kits on first use. Select a profile appropriate to the intended venue and submission year.

The [workflow guide](.agents/skills/smpl-papers/references/workflow.md) covers commands, file formats, source verification, figure critique, scored PDF review, trial execution and resuming earlier runs. The [role prompts](.agents/skills/smpl-papers/references/prompts.md) show what each Codex role reads, checks and delivers.

## Verification

```sh
python -m unittest discover -s tests
python tests/smoke_pdf.py --profiles starter
```

The automated checks cover provider responses, citation integrity, checkpoint invalidation, figure review, PDF approval and registered experiment execution. The smoke command compiles and renders a synthetic cited manuscript. Add `--output PATH` to retain its review artifacts.

## License

[MIT License](LICENSE).
