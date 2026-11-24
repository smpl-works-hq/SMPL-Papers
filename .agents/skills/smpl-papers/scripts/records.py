"""Shared storage and validation for run artifacts."""

import hashlib
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_hash(value):
    return digest(json.dumps(value, sort_keys=True, ensure_ascii=False).encode())


def file_hash(path):
    if not path.is_file():
        return None
    checksum = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


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


def write_json(path, value):
    write_atomic(path, (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode())


def get_records(root, filename, field):
    value = load_json(root / filename)[field]
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"{filename}: {field} must be a list of objects.")
    return value


def local_file(root, name, directory):
    parent = (root / directory).resolve()
    given = Path(name)
    path = (root / given).resolve()
    if (given.is_absolute() or not parent.is_relative_to(root.resolve())
            or not path.is_relative_to(parent) or not path.is_file()):
        raise ValueError(f"Expected an existing file in {directory}: {name}")
    return path.relative_to(root.resolve()).as_posix()


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", value):
        raise ValueError(f"Invalid identifier: {value!r}")
    return value


def required_text(record, *fields):
    for field in fields:
        if not isinstance(record.get(field), str) or not record[field].strip():
            raise ValueError(f"Expected nonempty text for {field}.")
