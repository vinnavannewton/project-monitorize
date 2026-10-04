#!/usr/bin/env python3
"""Synchronize Monitorize package versions from pyproject.toml."""

from __future__ import annotations

import os
import re
import stat
import sys
import tempfile
import tomllib
from datetime import datetime
from email.utils import format_datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*))?\Z")
DEB_TARGETS = {
    "debian-trixie": "trixie",
    "ubuntu-24.04": "noble",
    "ubuntu-26.04": "resolute",
}
MAINTAINER = "Monitorize contributors <noreply@example.com>"


class BumpError(Exception):
    pass


def read(relative: str) -> str:
    try:
        return (ROOT / relative).read_text(encoding="utf-8")
    except OSError as exc:
        raise BumpError(f"could not read {relative}: {exc.strerror}") from exc


def field(text: str, relative: str, pattern: str, label: str) -> re.Match[str]:
    matches = list(re.finditer(pattern, text, re.MULTILINE))
    if len(matches) != 1:
        raise BumpError(f"expected exactly one {label} field in {relative}")
    return matches[0]


def replace_field(text: str, match: re.Match[str], new: str) -> str:
    return text[:match.start(2)] + new + text[match.end(2):]


def check_version(actual: str, old: str, relative: str, label: str) -> None:
    if actual != old:
        raise BumpError(f"{relative} has {label}={actual}; expected {old}. Fix the existing mismatch before bumping.")


def prepare(new: str, now: datetime) -> tuple[str, dict[str, str]]:
    pyproject = read("pyproject.toml")
    try:
        old = tomllib.loads(pyproject)["project"]["version"]
    except (tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
        raise BumpError(f"could not read [project].version in pyproject.toml: {exc}") from exc
    if not isinstance(old, str) or not VERSION.fullmatch(old):
        raise BumpError(f"invalid current version in pyproject.toml: {old!r}")
    if old == new:
        raise BumpError(f"version is already {new}")
    match = field(pyproject, "pyproject.toml", r'^(version\s*=\s*")([^"]+)("\s*)$', "version")
    # The field must be the project's canonical value, not an unrelated TOML key.
    check_version(match.group(2), old, "pyproject.toml", "version")
    changes = {"pyproject.toml": replace_field(pyproject, match, new)}

    arch_path = "packaging/arch/PKGBUILD"
    arch = read(arch_path)
    match = field(arch, arch_path, r"^(pkgver=)([^\s#]+)(.*)$", "pkgver")
    check_version(match.group(2), old, arch_path, "pkgver")
    arch = replace_field(arch, match, new)
    release = field(arch, arch_path, r"^(pkgrel=)([^\s#]+)(.*)$", "pkgrel")
    if not re.fullmatch(r"[1-9][0-9]*", release.group(2)):
        raise BumpError(f"invalid pkgrel in {arch_path}: {release.group(2)}")
    changes[arch_path] = replace_field(arch, release, "1")

    nix_path = "nix/package.nix"
    nix = read(nix_path)
    match = field(nix, nix_path, r'^([ \t]*version[ \t]*=[ \t]*")([^"]+)("[ \t]*;[ \t]*)$', "Monitorize version")
    check_version(match.group(2), old, nix_path, "version")
    changes[nix_path] = replace_field(nix, match, new)

    for distro, initial_release in (("rpm", "1%{?dist}"), ("tumbleweed", "0")):
        relative = f"packaging/{distro}/monitorize.spec"
        spec = read(relative)
        match = field(spec, relative, r"^(Version:[ \t]+)(\S+)([ \t]*)$", "Version")
        check_version(match.group(2), old, relative, "Version")
        spec = replace_field(spec, match, new)
        release = field(spec, relative, r"^(Release:[ \t]+)(\S+)([ \t]*)$", "Release")
        release_pattern = r"[1-9][0-9]*%\{\?dist\}" if distro == "rpm" else r"[0-9]+"
        if not re.fullmatch(release_pattern, release.group(2)):
            raise BumpError(f"invalid Release in {relative}: {release.group(2)}")
        spec = replace_field(spec, release, initial_release)
        marker = "%changelog\n"
        if spec.count(marker) != 1:
            raise BumpError(f"missing or duplicate %changelog in {relative}")
        first_line = spec.split(marker, 1)[1].splitlines()[0]
        latest = re.fullmatch(r"\* .+ - (\d+(?:\.\d+){1,2})-(\d+)", first_line)
        if latest is None or latest.group(1) != old:
            raise BumpError(f"{relative} latest RPM changelog entry does not match current version {old}")
        release_number = "1" if distro == "rpm" else "0"
        entry = (f"* {now.strftime('%a %b %d %Y')} {MAINTAINER} - {new}-{release_number}\n"
                 f"- Release Monitorize {new}.\n\n")
        changes[relative] = spec.replace(marker, marker + entry, 1)

    relative = "packaging/tumbleweed/monitorize.changes"
    history = read(relative)
    if not history.startswith("-------------------------------------------------------------------\n"):
        raise BumpError(f"missing current changelog entry in {relative}")
    first_entry = history.split("-------------------------------------------------------------------", 2)[1]
    if not re.search(rf"(?m)^- .*?(?<![0-9.]){re.escape(old)}(?![0-9]|\.[0-9])", first_entry):
        raise BumpError(f"{relative} latest entry does not describe current version {old}")
    entry = (f"-------------------------------------------------------------------\n"
             f"{now.strftime('%a %b %d %Y')} {MAINTAINER}\n\n"
             f"- Release Monitorize {new}.\n\n")
    changes[relative] = entry + history

    for target, codename in DEB_TARGETS.items():
        relative = f"packaging/deb/{target}/debian/changelog"
        history = read(relative)
        match = re.match(r"monitorize \(([^)]+)\) (\S+); urgency=medium\n", history)
        if match is None:
            raise BumpError(f"missing current changelog header in {relative}")
        check_version(match.group(1), old, relative, "changelog version")
        if match.group(2) != codename:
            raise BumpError(f"{relative} has distribution {match.group(2)}; expected {codename}")
        entry = (f"monitorize ({new}) {codename}; urgency=medium\n\n"
                 f"  * Release Monitorize {new}.\n\n"
                 f" -- {MAINTAINER}  {format_datetime(now)}\n\n")
        changes[relative] = entry + history
    return old, changes


def write_all(changes: dict[str, str]) -> None:
    staged: dict[str, Path] = {}
    originals = {relative: (ROOT / relative).read_bytes() for relative in changes}
    written: list[str] = []
    try:
        for relative, content in changes.items():
            destination = ROOT / relative
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent,
                                             prefix=".bump-version-", delete=False) as tmp:
                tmp.write(content)
                staged[relative] = Path(tmp.name)
            os.chmod(staged[relative], stat.S_IMODE(destination.stat().st_mode))
        for relative, temporary in staged.items():
            os.replace(temporary, ROOT / relative)
            written.append(relative)
    except OSError:
        for relative in written:
            (ROOT / relative).write_bytes(originals[relative])
        raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


def main() -> int:
    if len(sys.argv) != 2 or not VERSION.fullmatch(sys.argv[1]):
        print("Usage: ./scripts/bump-version.py NEW_VERSION (for example, 0.33.1)", file=sys.stderr)
        return 2
    new = sys.argv[1]
    try:
        old, changes = prepare(new, datetime.now().astimezone())
        write_all(changes)
    except (BumpError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Monitorize version bump\n  old: {old}\n  new: {new}\n\nUpdated:")
    for relative in changes:
        print(f"  {relative}")
    print(f"\nVersion synchronized successfully: {new}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
