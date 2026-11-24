"""Build a reviewed manuscript with native TeX or TeX in WSL."""

import argparse
from io import BytesIO
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from urllib.request import urlopen
from zipfile import ZipFile

import run


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


def build(root, distro, cache):
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
    try:
        with TemporaryDirectory(prefix=".tex-", dir=root) as build_path:
            workspace = Path(build_path)
            source = workspace / "source"
            source.mkdir()
            profile = manifest["template"]
            prepare_template(profile, source, cache)
            for item in ("title.tex", "body.tex", "references.bib"):
                shutil.copyfile(root / item, source / item)
            # Copy only figures checked in the handoff manifest.
            for figure in run.get_records(root, "figures.json", "figures"):
                item = run.local_file(root, figure["path"], "figures")
                (source / item).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(root / item, source / item)
            shutil.copytree(source, workspace / "compile")
            pdf = compile_pdf(workspace / "compile", log, distro)
            if not run.current(root, manifest, "review"):
                raise ValueError("The reviewed inputs changed during compilation. Run review again.")
            archive = root / "latex-source"
            archive.mkdir(exist_ok=True)
            for file in source.rglob("*"):
                if file.is_file():
                    destination = archive / file.relative_to(source)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    run.write_atomic(destination, file.read_bytes())
            run.write_atomic(published_pdf, pdf)
            run.mark(root, manifest, "build")
    except Exception as exc:
        with log.open("a", encoding="utf-8") as output:
            output.write(f"\nBuild failed: {exc}\n")
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build_command = commands.add_parser("build")
    build_command.add_argument("run", type=Path)
    build_command.add_argument("--wsl-distro", help="Use this WSL distribution on Windows.")
    build_command.add_argument("--cache", type=Path, default=Path(".template-cache"))
    args = parser.parse_args()
    try:
        build(args.run.resolve(), args.wsl_distro, args.cache.resolve())
        print(args.run.resolve() / "paper.pdf")
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
