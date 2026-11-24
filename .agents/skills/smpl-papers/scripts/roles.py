"""Validate section assignments and assemble Codex role prompts."""

from datetime import date
import os
from pathlib import Path
import re

from records import identifier, load_json, local_file, required_text


PROMPTS = Path(__file__).resolve().parents[1] / "references" / "prompts.md"
ROUTES = {
    "outline": ("brainstorming-research-ideas",), "literature": ("citation-management", "literature-review"),
    "figures": ("academic-plotting", "scientific-visualization"),
    "writer": ("ml-paper-writing", "scientific-writing"), "revision": ("scientific-writing",),
    "methods": ("ara-rigor-reviewer",), "evidence": ("citation-management",),
    "presentation": ("scientific-writing",), "layout": ("pdf",),
    "plan": ("brainstorming-research-ideas",), "experiment": ("brainstorming-research-ideas",),
}
SPECIALTIES = {"training": ("ml-training-recipes", "fine-tuning-with-trl"),
               "evaluation": ("evaluating-llms-harness", "evaluating-code-models"),
               "tracking": ("mlflow", "weights-and-biases"), "systems": ("systems-paper-writing",)}


def workplan(root):
    plan = load_json(root / "workplan.json")
    if plan.get("citation_mode", "live") not in {"live", "fixture"}:
        raise ValueError("citation_mode must be live or fixture.")
    if plan.get("cutoff"):
        date.fromisoformat(plan["cutoff"])
    sections = plan["sections"]
    if not isinstance(sections, list) or not sections or not isinstance(plan["figures"], list):
        raise ValueError("List at least one section and supply a figures list.")
    section_ids = set()
    for section in sections:
        sid = identifier(section["id"])
        required_text(section, "title")
        queries = section["queries"]
        if sid in section_ids or not isinstance(queries, list) or any(
                not isinstance(q, str) or not q.strip() for q in queries) or len(set(queries)) != len(queries):
            raise ValueError(f"Duplicate section or invalid search queries: {sid}")
        section_ids.add(sid)
    ids = set()
    for fig in plan["figures"]:
        fid = identifier(fig["id"]).casefold()
        required_text(fig, "caption")
        if fid in ids or fig["section"] not in section_ids or fig["kind"] not in {
                "plot", "diagram", "supplied", "custom", "illustration"}:
            raise ValueError(f"Invalid figure assignment: {fid}")
        ids.add(fid)
        local_file(root, fig["source"], "materials")
    return plan


def specialist(role, directories, specialty=None):
    wanted = SPECIALTIES.get(specialty, ()) + ROUTES[role]
    roots = list(directories) + [Path.cwd() / ".agents/skills", Path(__file__).resolve().parents[2],
                                 Path.home() / ".agents/skills",
                                 Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "skills"]
    if os.environ.get("WSL_DISTRO_NAME"):
        for anchor in (Path.cwd(), Path(__file__).resolve()):
            roots.extend(parent / ".codex/skills" for parent in anchor.parents
                         if parent.parent.name.casefold() == "users" and str(parent).startswith("/mnt/"))
    found = {}
    for directory in dict.fromkeys(roots):
        for entry in sorted(directory.glob("*/SKILL.md")):
            try:
                with entry.open(encoding="utf-8-sig") as handle:
                    header = handle.read(2400).split("---", 2)
                match = re.search(r"(?m)^name:\s*['\"]?([\w-]+)", header[1]) if len(header) == 3 else None
            except (OSError, UnicodeError):
                continue
            if match and match[1] in wanted:
                found.setdefault(match[1], entry.resolve())
    return next((found[name] for name in wanted if name in found), None)


def brief(root, role, directories=(), specialty=None):
    blocks = re.split(r"(?m)^## ([a-z]+)\s*$", PROMPTS.read_text(encoding="utf-8"))
    instructions = dict(zip(blocks[1::2], blocks[2::2]))[role].strip()
    skill = specialist(role, directories, specialty)
    context = [f"Run directory: {root.resolve()}\nFile contracts: {PROMPTS.with_name('workflow.md')}", instructions]
    if skill:
        context.append(f"Read the installed specialist instructions at {skill} and apply them to this assignment.")
    if role in {"methods", "evidence", "presentation", "layout"} and (root / "preview.json").exists():
        preview = load_json(root / "preview.json")
        context.append(f"Review {preview['pdf']}. Its SHA-256 is {preview['sha256']}. "
                       f"Save reviews/{preview['id']}/{role}.json with pdf_sha256, score (1-5), "
                       "assessment, decision (approve, revise or blocked) and findings. "
                       "Each finding needs severity, location, issue and resolution.")
        if role == "layout":
            context.append("Include pages as one-based integers. Open these images:\n" + "\n".join(preview["pages"]))
    return "\n\n".join(context)
