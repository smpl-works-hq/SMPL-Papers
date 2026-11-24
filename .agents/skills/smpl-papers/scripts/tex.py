"""Compile a review preview and publish the exact PDF that passed review."""

import argparse
from io import BytesIO
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from urllib.request import urlopen
from uuid import uuid4
from zipfile import ZipFile

import run
from records import json_hash, write_json


ASSETS = Path(__file__).resolve().parents[1] / "assets"
MAX_ARCHIVE_SIZE = 8_000_000
TEX_TIMEOUT = 90


def extract_profile(archive, profile, destination):
    with ZipFile(BytesIO(archive)) as kit:
        for member in profile["members"]:
            matches = [info for info in kit.infolist()
                       if info.filename.endswith("/" + member) and "/" in info.filename
                       and len(info.filename.split("/")) == 2 and not info.is_dir()]
            if len(matches) != 1 or matches[0].file_size > MAX_ARCHIVE_SIZE:
                raise ValueError(f"Missing, ambiguous, or oversized author kit member: {member}")
            (destination / member).write_bytes(kit.read(matches[0]))


def prepare_template(name, destination, cache):
    shutil.copyfile(ASSETS / f"{name}.tex", destination / "paper.tex")
    if name == "starter":
        return
    profile = run.load_json(ASSETS / "author-kits.json")[name]
    archive_path = cache / f"{name}.zip"
    if archive_path.is_file():
        archive = archive_path.read_bytes()
    else:
        with urlopen(profile["url"], timeout=30) as response:
            archive = response.read(MAX_ARCHIVE_SIZE + 1)
    if len(archive) > MAX_ARCHIVE_SIZE or run.digest(archive) != profile["sha256"]:
        raise ValueError(f"{name} author kit failed checksum verification. Check {archive_path}.")
    if not archive_path.exists():
        cache.mkdir(parents=True, exist_ok=True)
        run.write_atomic(archive_path, archive)
    extract_profile(archive, profile, destination)


def tex_runner(directory, distro):
    if not distro and shutil.which("pdflatex") and shutil.which("bibtex"):
        return []
    if sys.platform != "win32" or not shutil.which("wsl.exe"):
        raise ValueError("Install pdflatex and bibtex or select an installed WSL distribution.")
    prefix = ["wsl.exe"]
    if distro:
        prefix.extend(["--distribution", distro])
    linux_path = subprocess.run(
        [*prefix, "--exec", "wslpath", "-a", str(directory)],
        check=True, capture_output=True, text=True, timeout=20
    ).stdout.strip()
    subprocess.run(
        [*prefix, "--exec", "sh", "-c", "command -v pdflatex && command -v bibtex"],
        check=True, capture_output=True, text=True, timeout=20,
    )
    return [*prefix, "--cd", linux_path, "--exec"]


def run_tex_command(prefix, directory, log, command):
    with log.open("a", encoding="utf-8") as output:
        output.write("\n$ " + " ".join(command) + "\n")
    result = subprocess.run([*prefix, *command], cwd=directory, timeout=TEX_TIMEOUT,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    with log.open("ab") as output:
        output.write(result.stdout)
    if result.returncode:
        raise ValueError(f"{command[0]} exited with code {result.returncode}. See {log}.")


def compile_pdf(directory, log, distro):
    prefix = tex_runner(directory, distro)
    latex = ["pdflatex", "-no-shell-escape", "-halt-on-error", "-interaction=nonstopmode", "paper.tex"]
    run_tex_command(prefix, directory, log, latex)
    aux = (directory / "paper.aux").read_text(encoding="utf-8", errors="replace")
    if "\\citation{" in aux:
        run_tex_command(prefix, directory, log, ["bibtex", "paper"])
    for _ in range(2):
        run_tex_command(prefix, directory, log, latex)
    final_log = (directory / "paper.log").read_text(encoding="utf-8", errors="replace")
    if re.search(r"(?:undefined (?:references|citations)|(?:Reference|Citation) .+ undefined)", final_log, re.I):
        raise ValueError(f"Unresolved LaTeX citations or cross-references. See {log}.")
    pdf = (directory / "paper.pdf").read_bytes()
    if (not pdf.startswith(b"%PDF-") or not pdf.rstrip().endswith(b"%%EOF")
            or not re.search(r"Output written on .+ \([1-9]\d* pages?,", final_log)):
        raise ValueError(f"TeX did not produce a valid PDF with pages. See {log}.")
    return pdf


def render_pages(pdf, destination):
    try:
        import pymupdf
    except ImportError as exc:
        raise ValueError("Install requirements-visual.txt to render PDF pages for review.") from exc
    pages = []
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        for index, page in enumerate(document, 1):
            name = f"page-{index}.png"
            page.get_pixmap(dpi=120, alpha=False).save(destination / name)
            pages.append(name)
    if not pages:
        raise ValueError("No pages rendered.")
    return pages


def preview(root, distro, cache):
    manifest = run.load_json(root / "run.json")
    if manifest["mode"] != "writing" or not run.current(root, manifest, "draft"):
        raise ValueError("A current draft is required to compile a preview.")
    key = uuid4().hex
    prefix = "previews/" + key
    folder = root / prefix
    folder.mkdir(parents=True)
    log = folder / "build.log"
    log.touch()
    source = folder / "source"
    source.mkdir()
    try:
        prepare_template(manifest["template"], source, cache)
        for name in ("title.tex", "body.tex", "references.bib"):
            shutil.copyfile(root / name, source / name)
        for figure in run.get_records(root, "figures.json", "figures"):
            name = run.local_file(root, figure["path"], "figures")
            (source / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / name, source / name)
        with TemporaryDirectory(prefix=".compile-", dir=root) as temp:
            workspace = Path(temp)
            shutil.copytree(source, workspace, dirs_exist_ok=True)
            pdf = compile_pdf(workspace, log, distro)
        pages = render_pages(pdf, folder)
        run.write_atomic(folder / "paper.pdf", pdf)
        if not run.current(root, manifest, "draft"):
            raise ValueError("Draft inputs changed during compilation.")
        info = {"id": key, "draft_hash": json_hash(manifest["stages"]["draft"]["fingerprint"]),
                "pdf": prefix + "/paper.pdf", "sha256": run.digest(pdf),
                "pages": [f"{prefix}/{p}" for p in pages],
                "sources": sorted(p.relative_to(root).as_posix() for p in source.rglob("*") if p.is_file())}
        (root / "reviews" / key).mkdir(parents=True)
        write_json(root / "preview.json", info)
        run.mark(root, manifest, "preview")
        return info
    except Exception as exc:
        with log.open("a", encoding="utf-8") as output:
            output.write(f"\nPreview failed: {exc}\n")
        raise ValueError(f"Preview failed. See {log}: {exc}") from exc


def build(root, distro=None, cache=None):
    manifest = run.load_json(root / "run.json")
    if manifest["mode"] != "writing" or not run.current(root, manifest, "review"):
        raise ValueError("A current approved writing review is required to build.")
    run.discard_downstream(manifest, "build")
    manifest["stages"]["build"] = {"status": "failed"}
    run.save(root, manifest)
    log = root / "build.log"
    log.write_text("", encoding="utf-8")
    published_pdf = root / "paper.pdf"
    if published_pdf.exists():
        published_pdf.replace(root / "paper.previous.pdf")
    wrote_pdf = False
    try:
        info = run.load_json(root / "preview.json")
        folder = (root / info["pdf"]).parent
        with TemporaryDirectory(prefix=".publish-", dir=root) as build_path:
            workspace = Path(build_path)
            source = workspace / "latex-source"
            shutil.copytree(folder / "source", source)
            pdf = (folder / "paper.pdf").read_bytes()
            if run.digest(pdf) != info["sha256"]:
                raise ValueError("Reviewed PDF changed.")
            if not run.current(root, manifest, "review"):
                raise ValueError("Reviewed inputs changed during publication.")
            shutil.copyfile(folder / "build.log", log)
            archive = root / "latex-source"
            if archive.exists():
                archive.replace(workspace / "previous-source")
            try:
                source.replace(archive)
            except OSError:
                if (workspace / "previous-source").exists():
                    (workspace / "previous-source").replace(archive)
                raise
            run.write_atomic(published_pdf, pdf)
            wrote_pdf = True
            run.mark(root, manifest, "build")
    except Exception as exc:
        if wrote_pdf:
            published_pdf.unlink(missing_ok=True)
        with log.open("a", encoding="utf-8") as output:
            output.write(f"\nBuild failed: {exc}\n")
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("preview", "build"):
        command = commands.add_parser(name)
        command.add_argument("run", type=Path)
        command.add_argument("--wsl-distro", help="Use this WSL distribution to compile the preview.")
        command.add_argument("--cache", type=Path, default=Path(".template-cache"))
    args = parser.parse_args()
    try:
        root = args.run.resolve()
        if args.command == "preview":
            print(root / preview(root, args.wsl_distro, args.cache.resolve())["pdf"])
        else:
            build(root, args.wsl_distro, args.cache.resolve())
            print(root / "paper.pdf")
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
