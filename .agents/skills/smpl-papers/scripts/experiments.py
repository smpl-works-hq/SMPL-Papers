"""Register a prediction before execution and record measured trial outcomes."""

import argparse
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from uuid import uuid4

from records import file_hash, identifier, json_hash, load_json, local_file, required_text, write_json


def plan_hash(root):
    import run
    manifest = load_json(root / "run.json")
    if manifest["mode"] != "experiment" or not run.current(root, manifest, "plan"):
        raise ValueError("Trials need an experiment run with a current registered plan.")
    return json_hash(manifest["stages"]["plan"]["fingerprint"])


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def register(root, spec):
    trial = identifier(spec["id"])
    required_text(spec, "hypothesis", "metric")
    if not isinstance(spec["command"], list) or not spec["command"] or any(
            not isinstance(s, str) or not s for s in spec["command"]):
        raise ValueError("Specify command as a nonempty argument list.")
    if (not all(finite(spec[k]) for k in ("favorable", "unfavorable"))
            or spec["favorable"] == spec["unfavorable"]):
        raise ValueError("Provide distinct finite favorable and unfavorable metric thresholds.")
    if not finite(spec.get("timeout_seconds", 300)) or spec.get("timeout_seconds", 300) <= 0:
        raise ValueError("timeout_seconds must be positive and finite.")
    registration = {"spec": spec, "plan_hash": plan_hash(root)}
    parent = root / "experiments"
    parent.mkdir(exist_ok=True)
    folder = parent / trial
    if not folder.exists():
        with TemporaryDirectory(dir=parent) as temporary:
            staging = Path(temporary) / "registration"
            staging.mkdir()
            write_json(staging / "registration.json", {**registration, "sha256": json_hash(registration),
                       "registered_at": datetime.now(timezone.utc).isoformat()})
            try:
                staging.rename(folder)
            except OSError:
                if not folder.exists():
                    raise
    saved = load_json(folder / "registration.json")
    if saved["sha256"] != json_hash(registration) or saved["spec"] != spec:
        raise ValueError("This trial ID already has a different registration. Use a new ID.")
    return folder


def registration(root, trial):
    folder = root / "experiments" / identifier(trial)
    record = load_json(folder / "registration.json")
    if (record["spec"]["id"] != trial or record["plan_hash"] != plan_hash(root)
            or record["sha256"] != json_hash({"spec": record["spec"], "plan_hash": record["plan_hash"]})):
        raise ValueError("Trial registration or its plan changed.")
    return folder, record


def outcome(spec, value):
    sign = 1 if spec["favorable"] > spec["unfavorable"] else -1
    if sign * value >= sign * spec["favorable"]:
        return "supported"
    if sign * value <= sign * spec["unfavorable"]:
        return "not-supported"
    return "inconclusive"


def checked_trial(root, folder, record):
    result = load_json(folder / "result.json")
    prefix = folder.relative_to(root).as_posix()
    log = local_file(root, result["log"], prefix)
    path = local_file(root, result["metrics"], prefix)
    value = load_json(root / path)[record["spec"]["metric"]]
    if (not finite(value) or result["registration"] != record["sha256"] or result["value"] != value
            or result["id"] != record["spec"]["id"] or result["metric"] != record["spec"]["metric"]
            or result["log_sha256"] != file_hash(root / log)
            or result["outcome"] != outcome(record["spec"], value) or result["metrics_sha256"] != file_hash(root / path)):
        raise ValueError("Recorded result differs from the registration or measured data.")
    return result, [prefix + "/registration.json", prefix + "/result.json", path, log]


def execute(root, trial):
    folder, record = registration(root, trial)
    spec = record["spec"]
    lock = folder / "running"
    try:
        handle = lock.open("x")
    except FileExistsError as exc:
        raise ValueError(f"Trial already running; inspect {lock} before recovering an interrupted run.") from exc
    try:
        with handle:
            handle.write(str(os.getpid()))
        if (folder / "result.json").exists():
            return checked_trial(root, folder, record)[0]
        attempt = folder / ("attempt-" + uuid4().hex)
        attempt.mkdir()
        shutil.copytree(root / "materials", attempt / "materials")
        command = list(spec["command"])
        if command[0] in {"python", "python3"}:
            command[0] = sys.executable
        metrics = attempt / "metrics.json"
        log = attempt / "execution.log"
        try:
            with log.open("wb") as output:
                execution = subprocess.run(command, cwd=attempt, stdout=output, stderr=subprocess.STDOUT,
                                           timeout=spec.get("timeout_seconds", 300), check=False,
                                           env={**os.environ, "SMPL_METRICS": str(metrics.resolve())})
            if execution.returncode:
                raise ValueError(f"Command exited with code {execution.returncode}.")
            value = load_json(metrics)[spec["metric"]]
            if not finite(value):
                raise ValueError("Expected a finite numeric metric.")
            if plan_hash(root) != record["plan_hash"]:
                raise ValueError("Plan changed during the trial.")
            result = {"id": trial, "registration": record["sha256"], "metric": spec["metric"], "value": value,
                      "outcome": outcome(spec, value), "log": log.relative_to(root).as_posix(), "log_sha256": file_hash(log),
                      "metrics": metrics.relative_to(root).as_posix(), "metrics_sha256": file_hash(metrics)}
            write_json(folder / "result.json", result)
            return result
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
            write_json(attempt / "failure.json", {"error": str(exc), "registration": record["sha256"]})
            raise ValueError(f"Trial failed. See {attempt}: {exc}") from exc
    finally:
        lock.unlink(missing_ok=True)


def summary(root):
    plan_hash(root)
    results, linked = [], []
    for path in sorted((root / "experiments").glob("*/registration.json")):
        folder, record = registration(root, path.parent.name)
        if not (folder / "result.json").exists():
            raise ValueError(f"{folder.name} has no completed measurement.")
        result, files = checked_trial(root, folder, record)
        results.append(result)
        linked.extend(files)
    if not results:
        raise ValueError("No completed trials.")
    return {"trials": results}, linked


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="action", required=True)
    for name in ("register", "execute", "summarize"):
        sub = subs.add_parser(name)
        sub.add_argument("run", type=Path)
        if name == "register":
            sub.add_argument("spec", type=Path)
        elif name == "execute":
            sub.add_argument("id")
    args = parser.parse_args()
    root = args.run.resolve()
    try:
        if args.action == "register":
            print(register(root, load_json(args.spec)))
        elif args.action == "execute":
            result = execute(root, args.id)
            print(f"{result['metric']}={result['value']} ({result['outcome']})")
        else:
            write_json(root / "experiments.json", summary(root)[0])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"Error: {exc}\n")
