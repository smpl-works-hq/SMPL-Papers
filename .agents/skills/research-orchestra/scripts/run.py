"""Manage manuscript artifacts and resumable checkpoints."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from tempfile import NamedTemporaryFile


TEMPLATES = ("starter", "cvpr2025", "iclr2025")
STAGES = {
    "plan": ((), ("protocol.md", "evidence-checklist.md")),
    "outline": ((), ("outline.md", "claims.json")),
    "literature": (("outline",), ("literature.json", "references.bib")),
    "figures": (("outline",), ("figures.json",)),
    "draft": (("literature", "figures"), ("title.tex", "body.tex")),
    "review": (("draft",), ("review.json",)),
    "build": (("review",), ("paper.pdf", "build.log", "latex-source/paper.tex")),
}
MODES = {"planning": ("plan",), "writing": tuple(STAGES)[1:]}
TEXT_INPUTS = {".txt", ".md", ".csv", ".tsv", ".json", ".tex", ".bib"}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_hash(path):
    if path.is_file():
        with path.open("rb") as file:
            return hashlib.file_digest(file, "sha256").hexdigest() if hasattr(
                hashlib, "file_digest"
            ) else digest(file.read())
    return None


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_atomic(path, data):
    with NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(data)
        except BaseException:
            handle.close()
            temporary.unlink()
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def save(root, manifest):
    data = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    write_atomic(root / "run.json", data.encode("utf-8"))


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
    # Each checkpoint includes the hashes of all of its transitive inputs.
    return all(file_hash(root / name) == checksum
               for name, checksum in record["fingerprint"].items())


def get_records(root, filename, field):
    value = load_json(root / filename)[field]
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"{filename}: {field} must be a list of objects.")
    return value


def local_file(root, name, directory):
    parent = (root / directory).resolve()
    given = Path(name)
    path = (root / given).resolve()
    if given.is_absolute() or not path.is_relative_to(parent) or not path.is_file():
        raise ValueError(f"Expected an existing file in {directory}: {name}")
    return path.relative_to(root.resolve()).as_posix()


def literature_keys(root):
    records = get_records(root, "literature.json", "sources")
    verified = []
    for source in records:
        if source.get("verified") is True:
            if any(not str(source.get(field, "")).strip()
                   for field in ("key", "url", "locator", "supports")):
                raise ValueError("Verified citations need a key, URL, locator, and supported statement.")
            verified.append(source["key"])
    bib = (root / "references.bib").read_text(encoding="utf-8")
    keys = re.findall(r"(?im)^\s*@(?!comment\b|string\b|preamble\b)\w+\s*[({]\s*([^,\s]+)\s*,", bib)
    if len(keys) != len(set(keys)) or len(verified) != len(set(verified)):
        raise ValueError("Duplicate bibliography or verified citation key.")
    if set(keys) != set(verified):
        raise ValueError("Bibliography keys must exactly match verified literature keys.")
    return set(verified)


def validate(root, stage):
    linked_files = []
    if stage == "outline":
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
        literature_keys(root)
    elif stage == "figures":
        for figure in get_records(root, "figures.json", "figures"):
            linked_files.append(local_file(root, figure["path"], "figures"))
            source = figure["source"]
            directory = "materials" if Path(source).parts[:1] == ("materials",) else "figures"
            linked_files.append(local_file(root, source, directory))
            if figure.get("script"):
                linked_files.append(local_file(root, figure["script"], "figures"))
            if not figure["caption"].strip():
                raise ValueError("Each figure needs a caption.")
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
    elif stage == "review":
        report = load_json(root / "review.json")
        if report["decision"] != "approve":
            raise ValueError(f"Review decision is {report['decision']!r}. Resolve it before building.")
        if any(item.get("severity") in {"major", "blocking", "critical"}
               and item.get("status") != "resolved" for item in report["findings"]):
            raise ValueError("An approved review still lists unresolved major findings.")
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
    for name in validate(root, stage):
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
    manifest["stages"][stage] = {"status": "complete", "fingerprint": fingerprint}
    save(root, manifest)


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
            command.add_argument("stage", choices=tuple(STAGES)[:-1])
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
            if args.command == "refresh":
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
