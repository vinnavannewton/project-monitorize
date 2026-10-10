#!/usr/bin/env python3
"""Canonical downloadable native package names and enabled CD targets."""

import argparse
import json
import re
from typing import NamedTuple


class Target(NamedTuple):
    label: str
    distro: str
    arch: str
    extension: str
    directory: str
    cd_enabled: bool = True


TARGETS = {
    "arch": Target("Arch Linux x86_64", "archlinux", "x86_64", "pkg.tar.zst", "dist/arch/x86_64"),
    "fedora-44": Target("Fedora 44 x86_64", "fedora-44", "x86_64", "rpm", "dist/rpm/fedora-44/x86_64"),
    "tumbleweed": Target("openSUSE Tumbleweed x86_64", "opensuse-tumbleweed", "x86_64", "rpm", "dist/rpm/tumbleweed/x86_64"),
    # Temporarily disabled in CD only. Local builds and version sync remain available.
    # Change cd_enabled to True to restore this release target.
    "debian-trixie": Target("Debian 13 AMD64", "debian-13", "amd64", "deb", "dist/deb/debian-trixie/amd64", cd_enabled=False),
    "ubuntu-24.04": Target("Ubuntu 24.04 AMD64", "ubuntu-24.04", "amd64", "deb", "dist/deb/ubuntu-24.04/amd64"),
    "ubuntu-26.04": Target("Ubuntu 26.04 AMD64", "ubuntu-26.04", "amd64", "deb", "dist/deb/ubuntu-26.04/amd64"),
}


def filename(target, version):
    if not re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*))?", version):
        raise ValueError(f"Invalid package version: {version}")
    spec = TARGETS[target]
    return f"monitorize-{version}-{spec.distro}-{spec.arch}.{spec.extension}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("name", "path", "matrix", "manifest"))
    parser.add_argument("arguments", nargs="*")
    args = parser.parse_args()
    enabled = [target for target, spec in TARGETS.items() if spec.cd_enabled]
    if args.command == "matrix" and not args.arguments:
        print(json.dumps({"include": [{"target": target, "label": TARGETS[target].label} for target in enabled]}))
    elif args.command == "manifest" and len(args.arguments) == 1:
        print(json.dumps([filename(target, args.arguments[0]) for target in enabled]))
    elif args.command in ("name", "path") and len(args.arguments) == 2:
        target, version = args.arguments
        if target not in TARGETS:
            parser.error(f"Unknown target: {target}")
        name = filename(target, version)
        print(name if args.command == "name" else f"{TARGETS[target].directory}/{name}")
    else:
        parser.error("Use matrix, manifest VERSION, or name/path TARGET VERSION")


if __name__ == "__main__":
    main()
