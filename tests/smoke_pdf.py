"""Compile a small cited document with each requested template profile."""

import argparse
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / ".agents/skills/smpl-papers/scripts"))
import run
import tex
import reviews
import sources
from records import write_json


def record_reviews(root, preview):
    """Synthetic reader records for testing the publication contract."""
    folder = root / "reviews" / preview["id"]
    for role in reviews.READERS:
        report = {"pdf_sha256": preview["sha256"], "score": 4, "decision": "approve",
                  "assessment": f"Synthetic {role} review for a pipeline fixture.", "findings": []}
        if role == "layout":
            report["pages"] = list(range(1, len(preview["pages"]) + 1))
        write_json(folder / (role + ".json"), report)


def compile_fixture(root, profile, distro, cache):
    fixture = PROJECT / "examples/lookup"
    manifest = run.initialize(root, "writing", profile, "utf-8-sig",
                              [fixture / "results.csv", fixture / "literature-fixture.md"])
    (root / "outline.md").write_text("# Latency by run")
    write_json(root / "claims.json", {"claims": [{"id": "C1", "kind": "measured",
               "statement": "The candidate averaged 10 ms across the three matched runs.",
               "evidence": [{"file": "results.csv", "locator": "candidate_ms, runs 1-3; (9+10+11)/3"}]}]})
    write_json(root / "workplan.json", {"citation_mode": "fixture",
               "sections": [{"id": "methods", "title": "Methods", "queries": ["arithmetic mean"]}],
               "figures": [{"id": "latency", "kind": "plot", "section": "methods", "caption": "Candidate latency by run.",
                            "source": "materials/results.csv", "x": "run", "y": "candidate_ms",
                            "xlabel": "Run", "ylabel": "Latency (ms)"}]})
    run.mark(root, manifest, "outline")
    subprocess.run([sys.executable, str(Path(run.__file__)), "prepare", str(root)], check=True, timeout=60)
    write_json(root / "citation-requests.json", {"sources": [{
        "key": "nist-mean", "section": "methods", "title": "NIST/SEMATECH e-Handbook of Statistical Methods",
        "author": "NIST/SEMATECH",
        "url": "https://www.itl.nist.gov/div898/handbook/eda/section3/eda351.htm",
        "fixture": "materials/literature-fixture.md", "locator": "Measures of Location, Mean",
        "quote": "the arithmetic mean is the sum of the values divided by the number of values",
        "supports": "Definition of the arithmetic mean."}]})
    sources.verify(root)
    (root / "related-work.md").write_text("The supplied NIST fixture defines the arithmetic mean.")
    run.mark(root, manifest, "literature")
    fig = run.get_records(root, "figures.json", "figures")[0]
    write_json(root / "figure-review.json", {"figures": [{"id": "latency", "sha256": fig["sha256"],
               "decision": "approve", "assessment": "Synthetic figure review for a rendering test."}]})
    run.mark(root, manifest, "figures")
    (root / "title.tex").write_text("Lookup latency on a synthetic fixture")
    (root / "body.tex").write_text(r"""\section{Methods}
We report the arithmetic mean \citep{nist-mean}. On the three fixture runs the
candidate averaged 10 ms. These measurements cover one synthetic dataset.
\begin{figure}[ht]\centering
\includegraphics[width=0.8\linewidth]{figures/latency.png}
\caption{Candidate latency on each of the three runs.}\end{figure}
""")
    run.mark(root, manifest, "draft")
    candidate = tex.preview(root, distro, cache)
    record_reviews(root, candidate)
    reviews.decide(root)
    run.mark(root, run.load_json(root / "run.json"), "review")
    tex.build(root, distro, cache)
    if run.file_hash(root / "paper.pdf") != candidate["sha256"]:
        raise RuntimeError("Published PDF differs from the reviewed candidate.")
    if not run.current(root, run.load_json(root / "run.json"), "build"):
        raise RuntimeError(f"{profile} build checkpoint is stale.")
    print(f"{profile}: passed ({(root / 'paper.pdf').stat().st_size} bytes)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", choices=run.TEMPLATES, nargs="+", default=run.TEMPLATES)
    parser.add_argument("--wsl-distro")
    parser.add_argument("--cache", type=Path, default=Path(".template-cache"))
    parser.add_argument("--output", type=Path, help="Keep the test runs in this directory.")
    args = parser.parse_args()
    # The caller's working directory is on a mount accessible from WSL.
    if args.output:
        for profile in args.profiles:
            compile_fixture(args.output.resolve() / profile, profile, args.wsl_distro, args.cache.resolve())
    else:
        with TemporaryDirectory(prefix=".tex-smoke-", dir=Path.cwd()) as temporary:
            for profile in args.profiles:
                compile_fixture(Path(temporary) / profile, profile, args.wsl_distro, args.cache.resolve())


if __name__ == "__main__":
    main()
