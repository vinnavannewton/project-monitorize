"""Discover hardware encoders that can produce an H.264 Sunshine stream."""

from __future__ import annotations

import csv
import glob
import re
import subprocess
from functools import lru_cache
from pathlib import Path


_PCI_ID = re.compile(
    r"^(?P<domain>[0-9a-fA-F]{4}|[0-9a-fA-F]{8}):"
    r"(?P<bus>[0-9a-fA-F]{2}):(?P<slot>[0-9a-fA-F]{2})\."
    r"(?P<function>[0-7])$"
)
_H264_VAAPI_ENCODE = re.compile(
    r"VAProfileH264High\s*:\s*VAEntrypointEncSlice", re.IGNORECASE
)


def normalize_pci_id(value: object) -> str:
    """Return a stable, canonical PCI address or an empty string."""
    match = _PCI_ID.fullmatch(str(value or "").strip())
    if not match:
        return ""
    domain = match.group("domain")[-4:]
    return (
        f"{domain}:{match.group('bus')}:{match.group('slot')}."
        f"{match.group('function')}"
    ).lower()


def _run(command: list[str]) -> str:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return f"{result.stdout}\n{result.stderr}"


def _render_nodes_by_pci() -> dict[str, str]:
    nodes = {}
    for render_node in sorted(glob.glob("/dev/dri/renderD*")):
        try:
            pci_id = normalize_pci_id(
                (Path("/sys/class/drm") / Path(render_node).name / "device")
                .resolve()
                .name
            )
        except OSError:
            continue
        if pci_id:
            nodes[pci_id] = render_node
    return nodes


def _vaapi_name(output: str, render_node: str) -> str:
    match = re.search(r"Driver version:\s*(.+)", output, re.IGNORECASE)
    if match:
        name = match.group(1).strip()
        if " for " in name:
            name = name.rsplit(" for ", 1)[1]
        name = re.sub(r"\s*\([^()]*(?:driver|drm|llvm)[^()]*\)\s*$", "", name,
                      flags=re.IGNORECASE)
        if name:
            return name
    return Path(render_node).name


@lru_cache(maxsize=1)
def discover_vaapi_h264_gpus() -> tuple[dict, ...]:
    """Return VA-API render nodes exposing H.264 High encode support."""
    devices = []
    for pci_id, render_node in sorted(_render_nodes_by_pci().items()):
        output = _run(["vainfo", "--display", "drm", "--device", render_node])
        if not _H264_VAAPI_ENCODE.search(output):
            continue
        name = _vaapi_name(output, render_node)
        devices.append(
            {
                "id": pci_id,
                "label": f"{name} ({pci_id})",
                "render_node": render_node,
                "cuda_index": "",
            }
        )
    return tuple(devices)


@lru_cache(maxsize=1)
def discover_nvidia_h264_gpus() -> tuple[dict, ...]:
    """Return NVIDIA devices whose driver reports an NVENC engine.

    Every NVENC generation supported by Sunshine provides H.264 encoding. A
    numeric encoder session count distinguishes an encoder-capable GPU from an
    NVIDIA display device whose driver reports the field as unavailable.
    """
    output = _run(
        [
            "nvidia-smi",
            "--query-gpu=index,pci.bus_id,name,encoder.stats.sessionCount",
            "--format=csv,noheader,nounits",
        ]
    )
    render_nodes = _render_nodes_by_pci()
    devices = []
    for row in csv.reader(output.splitlines()):
        if len(row) != 4:
            continue
        cuda_index, raw_pci_id, name, encoder_sessions = (
            value.strip() for value in row
        )
        pci_id = normalize_pci_id(raw_pci_id)
        if not cuda_index.isdigit() or not pci_id or not encoder_sessions.isdigit():
            continue
        devices.append(
            {
                "id": pci_id,
                "label": f"{name} ({pci_id})",
                "render_node": render_nodes.get(pci_id, ""),
                "cuda_index": cuda_index,
            }
        )
    return tuple(sorted(devices, key=lambda item: item["id"]))


def compatible_gpus(encoder: object) -> tuple[dict, ...]:
    clean_encoder = str(encoder or "").strip().lower()
    if clean_encoder in ("va-api", "vaapi"):
        return discover_vaapi_h264_gpus()
    if clean_encoder in ("nvidia", "nvenc", "nvidia nvenc"):
        return discover_nvidia_h264_gpus()
    return ()


def encoding_gpu_options(encoder: object) -> list[dict[str, str]]:
    """Return UI choices only when an encoder has multiple usable GPUs."""
    devices = compatible_gpus(encoder)
    if len(devices) < 2:
        return []
    return [
        {"id": "", "label": "Automatic (Sunshine)"},
        *({"id": device["id"], "label": device["label"]} for device in devices),
    ]


def resolve_encoding_gpu(encoder: object, requested_id: object) -> dict | None:
    """Resolve a saved PCI ID to its current render node and CUDA index."""
    pci_id = normalize_pci_id(requested_id)
    if not pci_id:
        return None
    return next(
        (device for device in compatible_gpus(encoder) if device["id"] == pci_id),
        None,
    )
