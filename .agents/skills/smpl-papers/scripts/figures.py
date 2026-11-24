"""Render workplan figures and check critiques against the displayed files."""

import argparse
import csv
from io import BytesIO
import math
from pathlib import Path

from records import file_hash, get_records, identifier, json_hash, load_json, local_file, required_text, write_atomic, write_json
from roles import workplan


def draw(spec, source):
    try:
        from matplotlib.figure import Figure
    except ImportError as exc:
        raise ValueError("Install requirements-visual.txt to draw plots or diagrams.") from exc
    figure = Figure(figsize=(7, 4), layout="constrained")
    ax = figure.subplots()
    if spec["kind"] == "plot":
        required_text(spec, "x", "y", "xlabel", "ylabel")
        with source.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        xs = [row[spec["x"]] for row in rows]
        ys = [float(row[spec["y"]]) for row in rows]
        if not ys or not all(math.isfinite(v) for v in ys):
            raise ValueError("Expected finite CSV measurements.")
        if spec.get("style", "bar") == "bar":
            if len(xs) != len(set(xs)):
                raise ValueError("Repeated bar labels need explicit aggregation or a custom plot.")
            ax.bar(xs, ys, color="#346d86")
        elif spec["style"] == "line":
            xvalues = [float(x) for x in xs]
            if not all(math.isfinite(x) for x in xvalues):
                raise ValueError("Expected finite line-plot coordinates.")
            ax.plot(xvalues, ys, marker="o", color="#346d86")
        else:
            raise ValueError("Plot style must be bar or line.")
        ax.set(xlabel=spec["xlabel"], ylabel=spec["ylabel"])
        ax.spines[["top", "right"]].set_visible(False)
    else:
        nodes = spec["nodes"]
        if not isinstance(nodes, list) or not nodes or len(set(nodes)) != len(nodes):
            raise ValueError("Give each diagram node a distinct label.")
        positions = {label: (math.cos(2 * math.pi * i / len(nodes)), math.sin(2 * math.pi * i / len(nodes)))
                     for i, label in enumerate(nodes)}
        for start, end in spec["edges"]:
            ax.annotate("", positions[end], positions[start],
                        arrowprops={"arrowstyle": "->", "shrinkA": 23, "shrinkB": 23, "color": "#344b55"})
        for label, (x, y) in positions.items():
            ax.text(x, y, str(label), ha="center", va="center", wrap=True,
                    bbox={"boxstyle": "round,pad=0.45", "facecolor": "#edf4f6", "edgecolor": "#346d86"})
        ax.set(xlim=(-1.45, 1.45), ylim=(-1.35, 1.35))
        ax.set_axis_off()
    output = BytesIO()
    figure.savefig(output, format="png", dpi=180)
    return output.getvalue()


def render(root, defer_custom=False):
    plan = workplan(root)
    folder = root / "figures"
    folder.mkdir(exist_ok=True)
    if (root / "figure-review.json").is_file() and (root / "figures.json").is_file():
        previous = {f.get("id"): f for f in get_records(root, "figures.json", "figures")}
        for critique in get_records(root, "figure-review.json", "figures"):
            item = previous.get(critique.get("id"))
            if (not item or not item.get("sha256") or item["sha256"] != critique.get("sha256")
                    or not (root / item["path"]).is_file()):
                continue
            path = root / local_file(root, item["path"], "figures")
            if file_hash(path) == item["sha256"]:
                version = root / "figure-history" / identifier(item["id"]) / item["sha256"]
                version.mkdir(parents=True, exist_ok=True)
                write_atomic(version / path.name, path.read_bytes())
                write_json(version / "critique.json", critique)
    result, pending = [], []
    for spec in plan["figures"]:
        fid, kind = spec["id"], spec["kind"]
        source = root / local_file(root, spec["source"], "materials")
        item = {k: spec[k] for k in ("id", "section", "source", "caption")}
        item["source_sha256"] = file_hash(source)
        if kind in {"custom", "illustration"}:
            recipe = "script" if kind == "custom" else "prompt"
            if defer_custom and not all((root / spec[k]).is_file() for k in ("file", recipe)):
                pending.append(fid)
                continue
            item["path"] = local_file(root, spec["file"], "figures")
            item[recipe] = local_file(root, spec[recipe], "figures")
            item[recipe + "_sha256"] = file_hash(root / item[recipe])
        else:
            suffix = source.suffix.lower() if kind == "supplied" else ".png"
            if suffix not in {".pdf", ".png", ".jpg", ".jpeg"}:
                raise ValueError(f"Unsupported supplied figure format: {suffix}")
            item["path"] = f"figures/{fid}{suffix}"
            content = source.read_bytes() if kind == "supplied" else draw(spec, source)
            write_atomic(root / item["path"], content)
        item["sha256"] = file_hash(root / item["path"])
        result.append(item)
    write_json(root / "figures.json", {"workplan_hash": json_hash(plan), "figures": result, "pending": pending})


def checked_figures(root):
    plan = workplan(root)
    if load_json(root / "figures.json").get("workplan_hash") != json_hash(plan):
        raise ValueError("Render the figures for the current workplan.")
    figures = get_records(root, "figures.json", "figures")
    critiques = get_records(root, "figure-review.json", "figures")
    ids = [f["id"] for f in plan["figures"]]
    if [f["id"] for f in figures] != ids or sorted(r["id"] for r in critiques) != sorted(ids):
        raise ValueError("Every planned figure needs one rendered output and one critique.")
    reviews = {r["id"]: r for r in critiques}
    linked = []
    for spec, fig in zip(plan["figures"], figures):
        review = reviews[fig["id"]]
        required_text(review, "assessment")
        if any(fig[field] != spec[field] for field in ("section", "source", "caption")):
            raise ValueError("Figure handoff differs from the workplan.")
        if fig["source_sha256"] != file_hash(root / fig["source"]):
            raise ValueError("Figure source changed. Render and inspect it again.")
        path = local_file(root, fig["path"], "figures")
        if (review["decision"] != "approve" or review["sha256"] != file_hash(root / path)
                or fig["sha256"] != review["sha256"]):
            raise ValueError(f"{fig['id']}: revise or inspect the current figure.")
        linked.extend((path, local_file(root, fig["source"], "materials")))
        for recipe in ("script", "prompt"):
            if fig.get(recipe):
                linked.append(local_file(root, fig[recipe], "figures"))
                if fig.get(recipe + "_sha256") != file_hash(root / fig[recipe]):
                    raise ValueError(f"Figure {recipe} changed. Render and inspect it again.")
    return linked


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    try:
        render(parser.parse_args().run.resolve())
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"Error: {exc}\n")
