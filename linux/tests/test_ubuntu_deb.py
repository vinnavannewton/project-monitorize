from __future__ import annotations

import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = PROJECT_ROOT / "packaging" / "deb" / "ubuntu-26.04"


def test_ubuntu_deb_builder_contract() -> None:
    builder = (PACKAGE_ROOT / "build.sh").read_text()

    assert 'readonly UBUNTU_VERSION=26.04' in builder
    assert 'readonly IMAGE="ubuntu:${UBUNTU_VERSION}"' in builder
    assert '[[ "$(uname -m)" == "x86_64" ]]' in builder
    assert "git submodule status --recursive" in builder
    assert "git status --porcelain" in builder
    assert "MONITORIZE_BUILD_JOBS must be a positive integer" in builder
    assert "dpkg-buildpackage -b -us -uc" in builder
    assert 'source_dir="$(find /work' in builder
    assert "apt-get install -y --no-install-recommends /tmp/monitorize.deb" in builder
    assert "apt-get purge -y monitorize" in builder
    assert "sudo cmake --install" not in builder
    assert "cmake --install" not in builder


def test_ubuntu_deb_metadata_matches_project_version_and_sunshine_pin() -> None:
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text()
    changelog = (PACKAGE_ROOT / "debian" / "changelog").read_text()
    sunshine_mk = (PACKAGE_ROOT / "sunshine.mk").read_text()
    pinned_sunshine = (
        (PROJECT_ROOT / ".gitmodules").read_text()
    )

    project_version = re.search(r'^version = "([^"]+)"$', pyproject, re.MULTILINE)
    package_version = re.search(r"^monitorize \(([^)]+)\)", changelog, re.MULTILINE)
    sunshine_commit = re.search(r"^SUNSHINE_COMMIT = ([0-9a-f]{40})$", sunshine_mk, re.MULTILINE)

    assert project_version is not None
    assert package_version is not None
    assert sunshine_commit is not None
    assert package_version.group(1) == project_version.group(1)
    assert "external/sunshine" in pinned_sunshine


def test_ubuntu_deb_runtime_is_private_and_sets_sunshine_overrides() -> None:
    rules = (PACKAGE_ROOT / "debian" / "rules").read_text()
    wrapper = (PACKAGE_ROOT / "monitorize-wrapper").read_text()
    control = (PACKAGE_ROOT / "debian" / "control").read_text()
    postinst = (PACKAGE_ROOT / "debian" / "postinst").read_text()
    ufw_profile = (PACKAGE_ROOT / "monitorize.ufw.profile").read_text()

    assert "/usr/libexec/monitorize/sunshine" in rules
    assert "/usr/share/monitorize/sunshine/assets" in rules
    assert "MONITORIZE_SUNSHINE_BIN=/usr/libexec/monitorize/sunshine" in wrapper
    assert "MONITORIZE_SUNSHINE_ASSETS_DIR=/usr/share/monitorize/sunshine/assets" in wrapper
    assert "python3-pyqt6.qtquick" in control
    assert "policykit-1" in control
    assert "pybuild-plugin-pyproject" in control
    assert "libxcb-shm0-dev" in control
    assert "addgroup --system monitorize-input" in postinst
    assert "modprobe uinput" in postinst
    assert "cmake --install" not in rules
    assert "/usr/libexec/monitorize/monitorize-system-setup" in rules
    assert "io.github.vinnavannewton.monitorize.system-setup.policy" in rules
    assert "debian/monitorize/etc/ufw/applications.d/monitorize" in rules
    assert "[Monitorize]" in ufw_profile
    assert "5353,47998:48010,49098:49110/udp" in ufw_profile
