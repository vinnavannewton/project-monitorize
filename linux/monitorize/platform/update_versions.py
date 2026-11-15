"""Read version metadata for the two Monitorize update channels."""

from importlib import metadata
import json
from pathlib import Path
import re
import tomllib
from urllib.request import urlopen

PROJECT_URL = "https://github.com/vinnavannewton/project-monitorize"
PROJECT_RELEASES = "https://api.github.com/repos/vinnavannewton/project-monitorize/releases?per_page=100"
VKMS_VERSION_URL = "https://raw.githubusercontent.com/vinnavannewton/monitorize-vkms/main/VERSION"
DESKTOP_TAG_PREFIX = "monitorize-v"


def version_key(value):
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"([0-9]{1,6})\.([0-9]{1,6})(?:\.([0-9]{1,6}))?", value)
    return tuple(int(part or 0) for part in match.groups()) if match else None


def project_version():
    source = Path(__file__).resolve().parents[3] / "pyproject.toml"
    try:
        return tomllib.loads(source.read_text())["project"]["version"]
    except (OSError, ValueError, KeyError):
        try:
            return metadata.version("monitorize")
        except metadata.PackageNotFoundError:
            return ""


def vkms_version():
    from monitorize.platform.vkms_management import VERSION
    try:
        return VERSION.read_text().strip()
    except (OSError, UnicodeError):
        return ""


def latest_project_release():
    with urlopen(PROJECT_RELEASES, timeout=4) as response:
        releases = json.loads(response.read(4 * 1024 * 1024))
    if not isinstance(releases, list):
        return "", ""
    candidates = []
    for release in releases:
        if not isinstance(release, dict):
            continue
        tag = release.get("tag_name", "")
        if (release.get("draft") or release.get("prerelease") or not release.get("published_at")
                or not isinstance(tag, str) or not tag.startswith(DESKTOP_TAG_PREFIX)):
            continue
        version = tag.removeprefix(DESKTOP_TAG_PREFIX)
        if key := version_key(version):
            candidates.append((key, version, tag))
    if not candidates:
        return "", ""
    _, version, tag = max(candidates)
    return version, f"{PROJECT_URL}/releases/tag/{tag}"


def latest_vkms_version():
    with urlopen(VKMS_VERSION_URL, timeout=4) as response:
        version = response.read(64).decode("ascii").strip()
    return (version if version_key(version) else ""), ""


def update_status(installed, latest="", url=""):
    current, upstream = version_key(installed), version_key(latest)
    state = "unknown"
    if current is not None and upstream is not None:
        if upstream > current:
            state = "available"
        elif upstream == current:
            state = "current"
    return {"installed": installed, "latest": latest, "state": state, "url": url}
