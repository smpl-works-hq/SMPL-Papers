"""Compile a small cited document with each requested template profile."""

import argparse
from pathlib import Path
import sys
from tempfile import TemporaryDirectory


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / ".agents/skills/smpl-papers/scripts"))
import run
import tex


def compile_fixture(root, profile, distro, cache):
    fixture = PROJECT / "examples/lookup"
    manifest = run.initialize(root, "writing", profile, "utf-8-sig",
                              [fixture / "results.csv", fixture / "literature-fixture.md"])
    stages = {
        "outline": {"outline.md": "# Template smoke test", "claims.json": '{"claims":[]}'},
        "literature": {
            "literature.json": '{"sources":[{"key":"nist-mean","url":'
                               '"https://www.itl.nist.gov/div898/handbook/eda/section3/eda351.htm",'
                               '"locator":"Measures of Location, Mean",'
                               '"supports":"The definition of the arithmetic mean.",'
                               '"verified":true}]}',
            "references.bib": "@manual{nist-mean,\n"
                              "  author = {{NIST/SEMATECH}},\n"
                              "  title = {e-Handbook of Statistical Methods}\n}\n",
        },
        "figures": {"figures.json": '{"figures":[]}'},
        "draft": {
            "title.tex": "Template Smoke Test",
            "body.tex": "\\section{Methods}\nWe use the arithmetic mean \\cite{nist-mean}.\n",
        },
        "review": {"review.json": '{"decision":"approve","findings":[]}'},
    }
    for stage, artifacts in stages.items():
        for name, value in artifacts.items():
            (root / name).write_text(value, encoding="utf-8")
        run.mark(root, manifest, stage)
    try:
        tex.build(root, distro, cache)
    except Exception:
        print((root / "build.log").read_text(encoding="utf-8", errors="replace")[-5000:],
              file=sys.stderr)
        raise
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
