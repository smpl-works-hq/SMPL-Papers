# Workflow and handoffs

Resolve the commands below under this skill's `scripts/` directory. `RUN` is a directory in the user's project. The coordinator owns checkpoints. Worker roles write separate artifacts. `run.py brief RUN ROLE` expands the [role prompt](prompts.md) with run paths and an installed specialist. Add `--skills-dir PATH` for another installation or `--specialty training|evaluation|tracking|systems` for a specialist task. The resolver searches project and user skill directories by frontmatter name.

## Run commands

`run.py next RUN` reads actual checkpoint and review state. It returns the ready worker prompts and the applicable helper. When both branches are ready their assignments are listed together. Commands in the helper field operate on the same run directory.

```sh
python run.py init RUN --mode writing --template starter notes.md results.csv
python run.py brief RUN outline
python run.py mark RUN outline
python run.py prepare RUN
python sources.py verify RUN
python run.py mark RUN literature
python run.py mark RUN figures
python run.py mark RUN draft
python tex.py preview RUN
python reviews.py RUN
python run.py mark RUN review
python tex.py build RUN
```

Perform the corresponding writing or inspection assignment before each checkpoint. Intake snapshots inputs with distinct basenames into `materials/`. For a chat-only question save a Markdown input first. Use `--encoding cp1252` for Windows-1252 text. Run `refresh` and `status` to resume. A changed source invalidates dependent checkpoints. First-version writing checkpoints appear stale. Reuse the original inputs and manuscript, add the new workplan and evidence records, and render a fresh preview before publishing again. Planning runs retain their original two deliverables.

## Outline and parallel work

Write `outline.md` and `claims.json`. Each member of `claims` has an `id`, `kind` and `statement`. A measured claim includes `evidence` entries such as `{"file":"results.csv","locator":"run 1, candidate_ms"}`. Other kinds are `literature`, `interpretation` and `hypothesis`.

`workplan.json` assigns work by section. `cutoff` is an optional inclusive ISO publication date. Default `citation_mode` is `live`. Use `fixture` for an explicitly supplied fixed corpus.

```json
{
  "citation_mode": "live",
  "sections": [
    {"id":"methods","title":"Methods","queries":["measurement of lookup latency"]},
    {"id":"results","title":"Results","queries":[]}
  ],
  "figures": [
    {"id":"latency","section":"results","kind":"plot","source":"materials/results.csv",
     "caption":"Candidate latency by run.","style":"bar","x":"run","y":"candidate_ms",
     "xlabel":"Run","ylabel":"Latency (ms)"}
  ]
}
```

`prepare` dispatches both branches concurrently. Retry one branch with `sources.py search RUN` or `figures.py RUN`. Default discovery tries Crossref and Semantic Scholar, deduplicates candidates per query and records provider failures in `discovery.json`. Select `--provider crossref` or `semantic-scholar` for one index. `S2_API_KEY` is optional. Explicit `--provider google` uses Gemini search grounding and checks returned titles against Crossref. Set `GEMINI_API_KEY` and `SMPL_SEARCH_MODEL` to a search-capable model enabled on the account. Remote discovery sends the section queries. Fixture mode performs discovery and verification offline.

## Literature

Read primary publications and write `citation-requests.json`. Each entry names the work as identified by its source, an exact excerpt, its location, the claim it supports and the section. Supply a primary HTML, text or PDF URL. For a paywall or mismatch, find an accessible copy of the same work or record the gap in `related-work.md`. Use an empty sources list when no citations apply.

```json
{"sources":[{"key":"rfc8259","section":"methods","title":"The JavaScript Object Notation (JSON) Data Interchange Format","doi":"10.17487/RFC8259","url":"https://www.rfc-editor.org/rfc/rfc8259.txt","locator":"Abstract","quote":"JSON defines a small set of formatting rules","supports":"The specification defines JSON formatting rules."}]}
```

`sources.py verify RUN` retrieves the primary document and checks identity and passage presence. For a DOI it also retrieves the registry entry and BibTeX from Crossref. For a source without a DOI it generates a URL entry using its checked title. With a cutoff, supply a source-visible ISO `published` date when registry metadata lacks a usable date. Fixture entries add `"fixture":"materials/literature-fixture.md"`. `literature.json`, `references.bib` and content-addressed `receipts/` capture the results. The reader judges whether the passage supports the scientific claim. Write a section-by-section `related-work.md` with verified keys, close alternatives and remaining gaps.

For URL entries, optional `author` and `year` strings must appear in the supplied or retrieved document. Preserve an institutional author when available. For a cutoff, the source must identify the date as its publication or issue date. A PDF locator such as `p. 3` also checks that the excerpt occurs on that PDF page.

## Figures

A `plot` takes CSV columns, axis labels and `style` of `bar` or `line`. Lines use numeric x coordinates. A `diagram` takes a list of distinct `nodes` and `edges` as pairs of labels. A `supplied` figure preserves an input PDF, PNG, or JPEG. A `custom` figure specifies an existing `file` and reproducible `script` in `figures/`. Generate these custom files before `prepare` or retry the figure branch afterwards. Each figure needs an input material, section and caption.

An `illustration` assigns a `file` and a `prompt` path under `figures/`. The figure role uses the host's image-generation tool and records both. `prepare` lists unfinished custom plots and illustrations as pending while the literature work proceeds. Run `figures.py RUN` after creating these assets. On rerender, prior images with matching critiques are archived under `figure-history/`.

Open each output in `figures.json` and compare it to its source. Record the assessment and displayed hash in `figure-review.json`. Preserve prior critiques during revisions. A changed image or outstanding revision prevents the figure checkpoint. With no figures use `{"figures":[]}`.

```json
{"figures":[{"id":"latency","sha256":"<hash from figures.json>","decision":"approve","assessment":"Compared all three bars with candidate_ms and inspected axis units."}]}
```

## Manuscript and PDF review

Write the title alone in `title.tex` and the manuscript in `body.tex`. Use verified BibTeX keys and run-relative figure filenames. Keep preamble and bibliography rendering in the selected template. `tex.py preview RUN` compiles the draft, saves its sources and log, and renders page images. `preview.json` identifies the PDF, pages and review directory. On Windows `--wsl-distro NAME` selects a WSL TeX installation.

Save four JSON records to `reviews/<preview-id>/` as `methods.json`, `evidence.json`, `presentation.json` and `layout.json`. Each reader inspects this PDF and its relevant evidence. The layout reader inspects every PNG and includes the one-based page numbers. Scores range from 1 (central problems) to 5 (ready on this dimension).

```json
{"pdf_sha256":"<hash from preview.json>","score":4,"assessment":"Describe what was checked and what the evidence showed.","decision":"revise","findings":[{"severity":"major","location":"Results, page 2","issue":"The stated speedup exceeds the measured range.","resolution":"Recalculate against the matched baseline."}]}
```

`reviews.py RUN` saves the scores, mean, findings, and decision in `review.json` and the version's review directory. Exit 2 means revise or blocked. Layout adds `"pages":[1,2]` for a two-page paper. An unresolved significant finding or blocking decision prevents publication regardless of mean score. Revisions receive fresh preview IDs and reviews. `tex.py build RUN` delivers the exact approved bytes and editable `latex-source/`.

## Experiments

Use `--mode planning` for a protocol or `--mode experiment` to run trials. Both start with `protocol.md`, `evidence-checklist.md` and `run.py mark RUN plan`. Include executable experiment code and input data in the original materials. Use `run.py brief RUN experiment --specialty evaluation`, adapting the specialty as needed.

```json
{"id":"lookup-1","hypothesis":"Indexing reduces latency on this fixed workload.","metric":"speedup","favorable":1.2,"unfavorable":1.0,"command":["python","materials/measure.py"],"timeout_seconds":60}
```

```sh
python experiments.py register RUN trial.json
python experiments.py execute RUN lookup-1
python experiments.py summarize RUN
python run.py mark RUN experiment
```

Register the hypothesis and thresholds before measurement. A smaller favorable threshold makes lower values better. The middle range is inconclusive. Execution runs the specified argument list in a fresh attempt directory with `materials/` snapshots. The command writes numeric metrics as a JSON object at the path in `SMPL_METRICS`. Logs, raw metrics and failed attempts remain on disk. A completed ID reuses its measurement and a failed attempt can be retried. Changed commands or hypotheses require new IDs. After summarizing, write `findings.md` with the measured outcome, limitations, next hypothesis or stopping reason. The registration, trial summary and raw metrics can become materials for a manuscript.
