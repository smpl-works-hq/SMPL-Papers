"""Combine scored reviews of a specific rendered manuscript."""

import argparse
from pathlib import Path

from records import file_hash, identifier, json_hash, load_json, local_file, required_text, write_json


READERS = ("methods", "evidence", "presentation", "layout")


def preview_files(root, manifest):
    item = load_json(root / "preview.json")
    prefix = "previews/" + identifier(item["id"])
    if item["draft_hash"] != json_hash(manifest["stages"]["draft"]["fingerprint"]):
        raise ValueError("Preview belongs to an earlier draft.")
    if item["pdf"] != prefix + "/paper.pdf" or file_hash(root / item["pdf"]) != item["sha256"]:
        raise ValueError("Preview PDF changed.")
    if not item["pages"] or not item["sources"]:
        raise ValueError("The preview needs rendered pages and editable sources.")
    paths = [local_file(root, item["pdf"], prefix), local_file(root, prefix + "/build.log", prefix)]
    for n, page in enumerate(item["pages"], 1):
        if page != f"{prefix}/page-{n}.png":
            raise ValueError("Preview pages must be consecutive.")
        paths.append(local_file(root, page, prefix))
    for source in item["sources"]:
        paths.append(local_file(root, source, prefix + "/source"))
    return paths


def collect(root):
    preview = load_json(root / "preview.json")
    folder = "reviews/" + identifier(preview["id"])
    ratings, findings, paths = {}, [], []
    decision = "approve"
    for role in READERS:
        path = local_file(root, f"{folder}/{role}.json", folder)
        record = load_json(root / path)
        required_text(record, "assessment")
        if record["pdf_sha256"] != preview["sha256"]:
            raise ValueError(f"{role}: review belongs to another PDF.")
        if type(record["score"]) is not int or not 1 <= record["score"] <= 5:
            raise ValueError(f"{role}: use a score from 1 to 5.")
        if record["decision"] not in {"approve", "revise", "blocked"} or not isinstance(record["findings"], list):
            raise ValueError(f"{role}: invalid review decision or findings.")
        if role == "layout" and sorted(record["pages"]) != list(range(1, len(preview["pages"]) + 1)):
            raise ValueError("The formatting review must cover every rendered page.")
        if record["decision"] == "blocked":
            decision = "blocked"
        elif record["decision"] == "revise" and decision == "approve":
            decision = "revise"
        for finding in record["findings"]:
            required_text(finding, "location", "issue", "resolution")
            if finding["severity"] not in {"minor", "major", "blocking", "critical"}:
                raise ValueError("Invalid finding severity.")
            if finding["severity"] != "minor" and finding.get("status") != "resolved" and decision == "approve":
                decision = "revise"
            findings.append({**finding, "reader": role})
        ratings[role] = record["score"]
        paths.append(path)
    return {"preview": preview["id"], "pdf_sha256": preview["sha256"], "decision": decision,
            "scores": ratings, "mean_score": sum(ratings.values()) / len(ratings),
            "findings": findings}, paths


def decide(root):
    import run
    if not run.current(root, load_json(root / "run.json"), "preview"):
        raise ValueError("Render the current draft before recording its reviews.")
    report, _ = collect(root)
    write_json(root / "reviews" / report["preview"] / "decision.json", report)
    write_json(root / "review.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    try:
        report = decide(parser.parse_args().run.resolve())
        print(f"{report['decision']} ({report['mean_score']:.2f}/5)")
        raise SystemExit(0 if report["decision"] == "approve" else 2)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"Error: {exc}\n")
