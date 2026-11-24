"""Manage manuscript artifacts and resumable checkpoints."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import sys

from records import digest, file_hash, get_records, json_hash, load_json, local_file, write_atomic, write_json
import roles


TEMPLATES = ("starter", "cvpr2025", "iclr2025")
STAGES = {
    "plan": ((), ("protocol.md", "evidence-checklist.md")),
    "experiment": (("plan",), ("experiments.json", "findings.md")),
    "outline": ((), ("outline.md", "claims.json", "workplan.json")),
    "literature": (("outline",), ("discovery.json", "citation-requests.json", "literature.json",
                                  "references.bib", "related-work.md")),
    "figures": (("outline",), ("figures.json", "figure-review.json")),
    "draft": (("literature", "figures"), ("title.tex", "body.tex")),
    "preview": (("draft",), ("preview.json",)),
    "review": (("preview",), ("review.json",)),
    "build": (("review",), ("paper.pdf", "build.log", "latex-source/paper.tex")),
}
MODES = {"planning": ("plan",), "experiment": ("plan", "experiment"), "writing": tuple(STAGES)[2:]}
TEXT_INPUTS = {".txt", ".md", ".csv", ".tsv", ".json", ".tex", ".bib"}


def save(root, manifest):
    write_json(root / "run.json", manifest)


def read_input(source, encoding):
    data = source.read_bytes()
    if source.suffix.lower() in TEXT_INPUTS:
        try:
            return data.decode(encoding).removeprefix("\ufeff").encode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"{source.name}: invalid {encoding}. Save as UTF-8 or use --encoding.") from exc
    return data


def initialize(root, mode, template, encoding, sources):
    originals = [path.resolve(strict=True) for path in sources]
    names = [path.name.casefold() for path in originals]
    if len(names) != len(set(names)):
        raise ValueError("Input files must have distinct basenames.")
    materials = [(source, read_input(source, encoding)) for source in originals]
    root.mkdir(parents=True)
    (root / "materials").mkdir()
    records = []
    for source, data in materials:
        destination = f"materials/{source.name}"
        (root / destination).write_bytes(data)
        records.append({"source": str(source), "path": destination, "sha256": digest(data)})
    manifest = {"mode": mode, "template": template, "encoding": encoding,
                "inputs": records, "stages": {}}
    save(root, manifest)
    return manifest


def refresh(root, manifest):
    # Read and decode all sources before replacing any snapshot.
    refreshed = [(record, read_input(Path(record["source"]), manifest["encoding"]))
                 for record in manifest["inputs"]]
    changed = False
    for record, data in refreshed:
        checksum = digest(data)
        snapshot = root / record["path"]
        if checksum != record["sha256"] or checksum != file_hash(snapshot):
            write_atomic(snapshot, data)
            record["sha256"] = checksum
            changed = True
    if changed:
        manifest["stages"].clear()
        save(root, manifest)
    return changed


def current(root, manifest, stage):
    record = manifest["stages"].get(stage)
    if record is None or record["status"] != "complete":
        return False
    if manifest["mode"] == "writing" and record.get("version", 1) < 2:
        return False
    if stage == "experiment" and (root / "experiments.json").is_file():
        registered = {p.parent.name for p in (root / "experiments").glob("*/registration.json")}
        if registered != {r["id"] for r in load_json(root / "experiments.json")["trials"]}:
            return False
    # Each checkpoint includes the hashes of all of its transitive inputs.
    return all(file_hash(root / name) == checksum
               for name, checksum in record["fingerprint"].items())


def literature_keys(root):
    from sources import verified_sources
    return verified_sources(root)[0]


def validate(root, stage, manifest):
    linked_files = []
    if stage == "outline":
        roles.workplan(root)
        claims = get_records(root, "claims.json", "claims")
        ids = set()
        for claim in claims:
            claim_id = claim["id"]
            if not claim_id or claim_id in ids or not claim["statement"].strip():
                raise ValueError("Claims need unique IDs and statements.")
            ids.add(claim_id)
            if claim["kind"] not in {"measured", "literature", "interpretation", "hypothesis"}:
                raise ValueError(f"{claim_id}: unknown claim kind.")
            if claim["kind"] == "measured":
                evidence = claim.get("evidence", [])
                if not evidence:
                    raise ValueError(f"{claim_id}: measured claim has no evidence.")
                for item in evidence:
                    if not item.get("locator", "").strip():
                        raise ValueError(f"{claim_id}: missing evidence locator.")
                    linked_files.append(local_file(root, f"materials/{item['file']}", "materials"))
    elif stage == "literature":
        from sources import verified_sources
        plan = roles.workplan(root)
        search = load_json(root / "discovery.json")
        planned = [(s["id"], q) for s in plan["sections"] for q in s["queries"]]
        completed = [(r["section"], r["query"]) for r in search["searches"]]
        if (search["plan_hash"] != json_hash(plan) or planned != completed
                or search["mode"] != plan.get("citation_mode", "live")
                or search["mode"] == "live" and any(not r["completed"] for r in search["searches"])):
            raise ValueError("Complete discovery for the current outline.")
        linked_files.extend(verified_sources(root)[1])
    elif stage == "figures":
        from figures import checked_figures
        linked_files.extend(checked_figures(root))
    elif stage == "draft":
        tex = "\n".join((root / name).read_text(encoding="utf-8")
                        for name in ("title.tex", "body.tex"))
        tex = re.sub(r"(?<!\\)%[^\n]*", "", tex)
        keys = {key.strip() for match in re.findall(
            r"\\(?:cite\w*|nocite)\*?(?:\s*\[[^\]]*\]){0,2}\s*\{([^}]+)\}", tex
        ) for key in match.split(",")}
        missing = keys - literature_keys(root)
        if missing:
            raise ValueError("Unverified citation keys: " + ", ".join(sorted(missing)))
    elif stage == "preview":
        from reviews import preview_files
        linked_files.extend(preview_files(root, manifest))
    elif stage == "review":
        from reviews import collect
        report = load_json(root / "review.json")
        if report["decision"] != "approve":
            raise ValueError(f"Review decision is {report['decision']!r}. Resolve it before building.")
        decision, paths = collect(root)
        if decision != report:
            raise ValueError("Approval disagrees with scored reviews or still has unresolved major findings.")
        linked_files.extend(paths)
    elif stage == "experiment":
        from experiments import summary
        result, paths = summary(root)
        if result != load_json(root / "experiments.json"):
            raise ValueError("Refresh the experiment summary from measured results.")
        linked_files.extend(paths)
    elif stage == "build":
        info = load_json(root / "preview.json")
        if file_hash(root / "paper.pdf") != info["sha256"]:
            raise ValueError("Final PDF differs from the reviewed preview.")
        src = (root / info["pdf"]).parent / "source"
        for path in info["sources"]:
            target = (Path("latex-source") / (root / path).relative_to(src)).as_posix()
            if file_hash(root / path) != file_hash(root / target):
                raise ValueError("Published source differs from the reviewed preview.")
            linked_files.append(target)
    return linked_files


def stage_fingerprint(root, manifest, stage):
    if stage not in MODES[manifest["mode"]]:
        raise ValueError(f"{stage} does not belong to {manifest['mode']} mode.")
    dependencies, files = STAGES[stage]
    fingerprint = {}
    for record in manifest["inputs"]:
        if file_hash(root / record["path"]) != record["sha256"]:
            raise ValueError("Material snapshot changed. Run refresh before proceeding.")
        fingerprint[record["path"]] = record["sha256"]
    for dependency in dependencies:
        if not current(root, manifest, dependency):
            raise ValueError(f"{stage} requires a current {dependency} checkpoint.")
        fingerprint.update(manifest["stages"][dependency]["fingerprint"])
    for name in files:
        checksum = file_hash(root / name)
        if checksum is None or ((root / name).stat().st_size == 0 and name != "references.bib"):
            raise ValueError(f"Missing or empty artifact: {name}")
        fingerprint[name] = checksum
    for name in validate(root, stage, manifest):
        fingerprint[name] = file_hash(root / name)
    return fingerprint


def discard_downstream(manifest, stage):
    affected = {stage}
    for candidate, (dependencies, _) in STAGES.items():
        if any(item in affected for item in dependencies):
            affected.add(candidate)
    for candidate in affected:
        manifest["stages"].pop(candidate, None)


def mark(root, manifest, stage):
    fingerprint = stage_fingerprint(root, manifest, stage)
    if manifest["stages"].get(stage, {}).get("fingerprint") != fingerprint:
        discard_downstream(manifest, stage)
    manifest["stages"][stage] = {"version": 2, "status": "complete", "fingerprint": fingerprint}
    save(root, manifest)


def next_work(root, manifest, directories=(), specialty=None):
    def assignments(stage, names, helper=None):
        command = None
        if helper:
            script, *arguments = helper.split()
            command = [sys.executable, str(Path(__file__).with_name(script)),
                       *(str(root.resolve()) if arg == "RUN" else arg for arg in arguments)]
        return {"stage": stage, "helper": command, "helper_order": "before" if stage == "research" else "after",
                "assignments": [
            {"role": name, "prompt": roles.brief(root, name, directories, specialty)} for name in names]}

    pending = [s for s in MODES[manifest["mode"]] if not current(root, manifest, s)]
    if not pending:
        return assignments("complete", [])
    stage = pending[0]
    if stage in {"plan", "outline", "experiment"}:
        return assignments(stage, [stage])
    if stage in {"literature", "figures"}:
        branches = [s for s in ("literature", "figures") if s in pending]
        plan_hash = json_hash(roles.workplan(root))
        search = load_json(root / "discovery.json") if (root / "discovery.json").is_file() else {}
        figures = load_json(root / "figures.json") if (root / "figures.json").is_file() else {}
        need_search = "literature" in branches and (search.get("plan_hash") != plan_hash or
                      search.get("mode") == "live" and any(not q["completed"] for q in search["searches"]))
        need_figures = "figures" in branches and (figures.get("workplan_hash") != plan_hash or any(
            file_hash(root / f["source"]) != f["source_sha256"] or file_hash(root / f["path"]) != f["sha256"]
            for f in figures.get("figures", [])))
        helper = None
        if need_search and need_figures:
            helper = "run.py prepare RUN"
        elif need_search:
            helper = "sources.py search RUN"
        elif need_figures:
            helper = "figures.py RUN"
        return assignments("research", branches, helper)
    if stage == "draft":
        return assignments(stage, ["writer"])
    if stage == "preview":
        return assignments(stage, [], "tex.py preview RUN")
    if stage == "review":
        from reviews import READERS, collect
        preview = load_json(root / "preview.json")
        folder = root / "reviews" / preview["id"]
        missing = [role for role in READERS if not (folder / (role + ".json")).is_file()]
        if missing:
            return assignments(stage, missing, "reviews.py RUN")
        aggregate, _ = collect(root)
        if not (root / "review.json").exists() or load_json(root / "review.json") != aggregate:
            return assignments(stage, [], "reviews.py RUN")
        if aggregate["decision"] == "approve":
            return assignments(stage, [], "run.py mark RUN review")
        return assignments(aggregate["decision"], ["revision"] if aggregate["decision"] == "revise" else [])
    return assignments("build", [], "tex.py build RUN")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Create a run and snapshot its supplied inputs.")
    init.add_argument("run", type=Path)
    init.add_argument("--mode", choices=MODES, required=True)
    init.add_argument("--template", choices=TEMPLATES, default="starter")
    init.add_argument("--encoding", choices=("utf-8-sig", "cp1252"), default="utf-8-sig")
    init.add_argument("inputs", nargs="+", type=Path)
    for name in ("status", "refresh", "mark"):
        command = commands.add_parser(name)
        command.add_argument("run", type=Path)
        if name == "mark":
            command.add_argument("stage", choices=[s for s in STAGES if s not in {"preview", "build"}])
    for name in ("brief", "next"):
        dispatch = commands.add_parser(name, help="Show a role brief or the next ready assignments.")
        dispatch.add_argument("run", type=Path)
        if name == "brief":
            dispatch.add_argument("role", choices=roles.ROUTES)
        dispatch.add_argument("--skills-dir", action="append", default=[], type=Path)
        dispatch.add_argument("--specialty", choices=roles.SPECIALTIES)
    prepare = commands.add_parser("prepare", help="Dispatch section searches and figure rendering concurrently.")
    prepare.add_argument("run", type=Path)
    prepare.add_argument("--provider", choices=("both", "crossref", "semantic-scholar", "google"), default="both")
    # argparse's regular subcommand parser does not intermix optional arguments
    # with a trailing variadic positional. Parse the remaining input files here.
    args, trailing = parser.parse_known_args()
    if trailing:
        if args.command != "init" or any(item.startswith("-") for item in trailing):
            parser.error("unrecognized arguments: " + " ".join(trailing))
        args.inputs.extend(map(Path, trailing))
    root = args.run.resolve()
    try:
        if args.command == "init":
            initialize(root, args.mode, args.template, args.encoding, args.inputs)
        else:
            manifest = load_json(root / "run.json")
            if args.command == "brief":
                print(roles.brief(root, args.role, args.skills_dir, args.specialty))
            elif args.command == "next":
                print(json.dumps(next_work(root, manifest, args.skills_dir, args.specialty), indent=2))
            elif args.command == "prepare":
                import figures
                import sources
                if not current(root, manifest, "outline"):
                    raise ValueError("Prepare needs a current outline.")
                with ThreadPoolExecutor(max_workers=2) as pool:
                    search = pool.submit(sources.discover, root, args.provider)
                    draw = pool.submit(figures.render, root, defer_custom=True)
                    search.result()
                    draw.result()
                print("Wrote discovery.json and figures.json.")
            elif args.command == "refresh":
                print("Inputs refreshed." if refresh(root, manifest) else "Inputs are unchanged.")
            elif args.command == "mark":
                mark(root, manifest, args.stage)
            else:
                for stage in MODES[manifest["mode"]]:
                    logged = manifest["stages"].get(stage, {})
                    state = ("complete" if current(root, manifest, stage)
                             else "stale" if logged.get("status") == "complete"
                             else logged.get("status", "pending"))
                    print(f"{stage}: {state}")
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
