#!/usr/bin/env python3
"""Remove only byte-identical, already published Python artifacts from an upload set."""
import hashlib
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request


def filter_existing(directory, version, fetch):
    duplicate_paths = []
    for project in ("watasu", "watasu-code-interpreter"):
        stem = project.replace("-", "_")
        names = [f"{stem}-{version}-py3-none-any.whl", f"{stem}-{version}.tar.gz"]
        if any(not (directory / name).is_file() for name in names):
            raise ValueError(f"Missing built artifacts for {project}")
        metadata = fetch(f"https://pypi.org/pypi/{project}/{version}/json")
        for artifact in metadata.get("urls", []):
            name = artifact["filename"]
            if name not in names:
                raise ValueError(f"Unexpected published artifact for {project}")
            path = directory / name
            if hashlib.sha256(path.read_bytes()).hexdigest() != artifact["digests"]["sha256"]:
                raise ValueError(f"Published artifact differs from immutable source: {name}")
            duplicate_paths.append(path)
    for path in duplicate_paths:
        path.unlink()
        print(f"Verified already published artifact: {path.name}")
    return any(directory.glob("*.whl")) or any(directory.glob("*.tar.gz"))


def fetch_json(url):
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return {}
        raise


if __name__ == "__main__":
    remaining = filter_existing(Path("dist"), sys.argv[1], fetch_json)
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(f"remaining={str(remaining).lower()}\n")
