"""Sunshine service helper functions.

Detects, launches, and checks the status of the Sunshine GameStream server.
"""

import atexit
import ctypes
import json
import os
import shutil
import signal
import socket
import subprocess
import time
import webbrowser

PR_SET_PDEATHSIG = 1
_SUNSHINE_PROCESS: subprocess.Popen | None = None
_SUNSHINE_PROCESSES: dict[int, subprocess.Popen] = {}


def _set_pdeathsig() -> None:
    """Set Linux parent-death signal on the child process to guarantee termination if Monitorize dies."""
    try:
        libc = ctypes.CDLL(None)
        libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM)
    except Exception:
        try:
            libc = ctypes.CDLL("libc.so.6")
            libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM)
        except Exception:
            pass


SUNSHINE_BASE_PORT = 47989
SUNSHINE_HTTPS_PORT = 47990
SUNSHINE_HTTP_PORT = 47989
SUNSHINE_WEB_URL = f"https://localhost:{SUNSHINE_HTTPS_PORT}"


def get_sunshine_log_size(instance: int = 1) -> int:
    """Return the current Sunshine log size for reading only new diagnostics."""
    try:
        return os.path.getsize(os.path.join(get_sunshine_config_dir(instance), "sunshine.log"))
    except OSError:
        return 0


def get_sunshine_strict_selection_error(instance: int = 1, offset: int = 0) -> str:
    """Return a Monitorize strict-selection failure written after ``offset``."""
    log_file = os.path.join(get_sunshine_config_dir(instance), "sunshine.log")
    try:
        with open(log_file, "rb") as f:
            if offset > 0 and f.seek(0, os.SEEK_END) >= offset:
                f.seek(offset)
            else:
                f.seek(0)
            lines = f.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
        if "MONITORIZE_STRICT_" in line:
            return line.strip()
    return ""


def get_sunshine_port(instance: int = 1) -> int:
    """Return the base TCP port for a given Sunshine instance."""
    inst = int(instance) if isinstance(instance, (int, str)) and str(instance).isdigit() else 1
    return 47989 if inst == 1 else 49089


def get_sunshine_https_port(instance: int = 1) -> int:
    """Return the HTTPS Web UI port for a given Sunshine instance."""
    return get_sunshine_port(instance) + 1


def get_sunshine_web_url(instance: int = 1) -> str:
    """Return the local HTTPS URL for a given Sunshine instance."""
    return f"https://localhost:{get_sunshine_https_port(instance)}"


def get_sunshine_config_dir(instance: int = 1) -> str:
    """Return the isolated configuration directory for Monitorize's Sunshine engine."""
    override = os.environ.get("SUNSHINE_CONFIG_DIR")
    if override and instance == 1:
        return override
    config_home = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    return os.path.join(config_home, "monitorize", f"sunshine-{instance}")


def get_sunshine_config_path(instance: int = 1) -> str:
    """Return the absolute path to the active isolated sunshine.conf file."""
    return os.path.join(get_sunshine_config_dir(instance), "sunshine.conf")


def is_sunshine_running(instance: int = 1, timeout: float = 0.5) -> bool:
    """Check whether Monitorize's Sunshine instance is currently running."""
    proc = _SUNSHINE_PROCESSES.get(instance)
    if proc is None and instance == 1:
        proc = _SUNSHINE_PROCESS
    if proc is not None and proc.poll() is None:
        return True

    https_port = get_sunshine_https_port(instance)
    http_port = get_sunshine_port(instance)
    for port in (https_port, http_port):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(timeout)
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    return True
        except OSError:
            pass

    return False


def get_sunshine_process(instance: int = 1) -> subprocess.Popen | None:
    """Return the underlying subprocess.Popen for a Sunshine instance if managed by Monitorize."""
    proc = _SUNSHINE_PROCESSES.get(instance)
    if proc is None and instance == 1:
        proc = _SUNSHINE_PROCESS
    return proc


def get_sunshine_exit_code(instance: int = 1) -> int | None:
    """Return the exit code of Sunshine if terminated, or None if still running / not tracked."""
    proc = get_sunshine_process(instance)
    if proc is not None:
        return proc.poll()
    return None


def get_sunshine_last_error(instance: int = 1, max_lines: int = 5) -> str:
    """Read the last Fatal/Error line from sunshine.log for user diagnostics."""
    config_dir = get_sunshine_config_dir(instance)
    log_file = os.path.join(config_dir, "sunshine.log")
    if not os.path.isfile(log_file):
        return ""
    errors = []
    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
            for line in reversed(lines[-60:]):
                stripped = line.strip()
                if "Fatal:" in stripped or "Error:" in stripped or "SIGABRT" in stripped:
                    errors.append(stripped)
                    if len(errors) >= max_lines:
                        break
    except OSError:
        pass
    if errors:
        return " | ".join(reversed(errors))
    return ""


def check_sunshine_health(instance: int = 1) -> tuple[bool, int | None, str]:
    """Check if Sunshine instance is alive.

    Returns:
        tuple[bool, int | None, str]: (is_alive, exit_code, last_error_message)
    """
    proc = get_sunshine_process(instance)
    if proc is not None:
        code = proc.poll()
        if code is None:
            return True, None, ""


        if is_sunshine_running(instance):
            return True, None, ""
        err = get_sunshine_last_error(instance)
        return False, code, err

    if is_sunshine_running(instance):
        return True, None, ""

    return False, None, get_sunshine_last_error(instance)


def get_sunshine_candidates(instance: int = 1) -> list[list[str]]:
    """Return an ordered list of candidate commands to launch Monitorize's Sunshine engine."""
    candidates: list[list[str]] = []
    config_file = get_sunshine_config_path(instance)

    explicit_bin = os.environ.get("MONITORIZE_SUNSHINE_BIN", "").strip()
    if explicit_bin and os.path.isfile(explicit_bin) and os.access(explicit_bin, os.X_OK):
        candidates.append([explicit_bin, config_file])

    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    venv_sunshine = os.path.join(project_root, "linux", "venv", "bin", "sunshine")
    build_sunshine = os.path.join(project_root, "external", "sunshine", "build", "sunshine")

    for local_bin in (venv_sunshine, build_sunshine):
        if os.path.isfile(local_bin) and os.access(local_bin, os.X_OK):
            cmd = [local_bin, config_file]
            if cmd not in candidates:
                candidates.append(cmd)

    sunshine_bin = shutil.which("sunshine")
    if sunshine_bin:
        cmd = [sunshine_bin, config_file]
        if cmd not in candidates:
            candidates.append(cmd)

    for common_path in (
        "/usr/bin/sunshine",
        "/usr/local/bin/sunshine",
        "/opt/sunshine/sunshine",
        "/var/lib/flatpak/exports/bin/dev.lizardbyte.sunshine",
        os.path.expanduser("~/.local/share/flatpak/exports/bin/dev.lizardbyte.sunshine"),
        os.path.expanduser("~/.local/bin/sunshine"),
    ):
        if os.path.isfile(common_path) and os.access(common_path, os.X_OK):
            cmd = [common_path, config_file]
            if cmd not in candidates:
                candidates.append(cmd)

    return candidates


def get_sunshine_assets_dir(command: str = "") -> str | None:
    """Resolve assets for the packaged, installed, or development Sunshine binary."""
    explicit_assets = os.environ.get("MONITORIZE_SUNSHINE_ASSETS_DIR", "").strip()
    if explicit_assets and os.path.isdir(explicit_assets):
        return explicit_assets

    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    venv_bin = os.path.join(project_root, "linux", "venv", "bin", "sunshine")
    venv_assets = os.path.join(project_root, "linux", "venv", "share", "monitorize", "sunshine", "assets")
    build_bin = os.path.join(project_root, "external", "sunshine", "build", "sunshine")
    build_assets = os.path.join(project_root, "external", "sunshine", "build", "assets")

    ordered = ((venv_bin, venv_assets), (build_bin, build_assets))
    for binary, assets in ordered:
        if command == binary and os.path.isdir(assets):
            return assets
    for _, assets in ordered:
        if os.path.isdir(assets):
            return assets
    return None


def find_sunshine_command(instance: int = 1) -> list[str] | None:
    """Find the first available command to start Sunshine."""
    candidates = get_sunshine_candidates(instance)
    return candidates[0] if candidates else None


def get_sunshine_device_name(instance: int = 1) -> str:
    """Return the advertised Sunshine host name formatted as '<Hostname> Monitor <Instance>'."""
    try:
        raw_host = socket.gethostname().strip()
        host = raw_host.split(".")[0] if raw_host else "Monitorize"
    except Exception:
        host = "Monitorize"
    return f"{host} Monitor {instance}"


def ensure_sunshine_tray_disabled(instance: int = 1) -> None:
    """Ensure sunshine.conf has dedicated non-clashing port and permanently disabled tray."""
    config_dir = get_sunshine_config_dir(instance)
    config_path = get_sunshine_config_path(instance)
    base_port = get_sunshine_port(instance)
    name_val = get_sunshine_device_name(instance)
    config_home = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    profile_parent = os.path.join(config_home, "monitorize", f"sunshine-profile-{instance}")
    profile_sunshine_dir = os.path.join(profile_parent, "sunshine")

    for d in (config_dir, profile_parent, profile_sunshine_dir):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass
    lines = []
    has_tray = False
    has_port = False
    has_origin = False
    has_name = False
    if os.path.isfile(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped.startswith("system_tray"):
                        lines.append("system_tray = disabled\n")
                        has_tray = True
                    elif stripped.startswith("port"):
                        lines.append(f"port = {base_port}\n")
                        has_port = True
                    elif stripped.startswith("sunshine_name"):
                        lines.append(f"sunshine_name = {name_val}\n")
                        has_name = True
                    elif stripped.startswith("origin_pin_allowed"):
                        continue
                    elif stripped.startswith("origin_web_ui_allowed"):
                        lines.append("origin_web_ui_allowed = lan\n")
                        has_origin = True
                    else:
                        lines.append(line)
        except OSError:
            pass
    if not has_name:
        lines.append(f"sunshine_name = {name_val}\n")
    if not has_tray:
        lines.append("system_tray = disabled\n")
    if not has_port:
        lines.append(f"port = {base_port}\n")
    if not has_origin:
        lines.append("origin_web_ui_allowed = lan\n")
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
    except OSError:
        pass


    apps_json_path = os.path.join(config_dir, "apps.json")
    profile_apps_json = os.path.join(profile_sunshine_dir, "apps.json")
    default_apps = {
        "apps": [
            {
                "image-path": "desktop.png",
                "name": "Desktop",
            }
        ],
        "env": {
            "PATH": "$(PATH):$(HOME)/.local/bin"
        }
    }
    for p in (apps_json_path, profile_apps_json):
        if not os.path.exists(p):
            try:
                with open(p, "w", encoding="utf-8") as f:
                    json.dump(default_apps, f, indent=4)
            except OSError:
                pass

    if instance == 1:

        legacy_sunshine_dir = os.path.join(config_home, "monitorize", "sunshine")
        if os.path.isdir(legacy_sunshine_dir):
            for filename in ("sunshine_state.json",):
                src = os.path.join(legacy_sunshine_dir, filename)
                dst = os.path.join(profile_sunshine_dir, filename)
                if os.path.isfile(src) and not os.path.isfile(dst):
                    try:
                        shutil.copy2(src, dst)
                    except OSError:
                        pass
            legacy_creds = os.path.join(legacy_sunshine_dir, "credentials")
            profile_creds = os.path.join(profile_sunshine_dir, "credentials")
            if os.path.isdir(legacy_creds) and not os.path.isdir(profile_creds):
                try:
                    shutil.copytree(legacy_creds, profile_creds, dirs_exist_ok=True)
                except OSError:
                    pass
            elif os.path.isdir(legacy_creds) and os.path.isdir(profile_creds):
                for cert_file in ("cacert.pem", "cakey.pem"):
                    src = os.path.join(legacy_creds, cert_file)
                    dst = os.path.join(profile_creds, cert_file)
                    if os.path.isfile(src) and not os.path.isfile(dst):
                        try:
                            shutil.copy2(src, dst)
                        except OSError:
                            pass


_SUNSHINE_PIPEWIRE_NODES: dict[int, int] = {}
_SUNSHINE_PIPEWIRE_OFFSETS: dict[int, tuple[int, int]] = {}
_SUNSHINE_PIPEWIRE_DIMS: dict[int, tuple[int, int]] = {}


def set_sunshine_pipewire_node(
    node_id: int | str | None,
    instance: int = 1,
    offset_x: int = 0,
    offset_y: int = 0,
    width: int | None = None,
    height: int | None = None,
) -> None:
    """Set the PipeWire stream node id, offset, and dimensions for direct capture."""
    if node_id is not None and str(node_id).isdigit() and int(node_id) > 0:
        _SUNSHINE_PIPEWIRE_NODES[instance] = int(node_id)
        _SUNSHINE_PIPEWIRE_OFFSETS[instance] = (int(offset_x), int(offset_y))
        if width and height:
            _SUNSHINE_PIPEWIRE_DIMS[instance] = (int(width), int(height))
    else:
        _SUNSHINE_PIPEWIRE_NODES.pop(instance, None)
        _SUNSHINE_PIPEWIRE_OFFSETS.pop(instance, None)
        _SUNSHINE_PIPEWIRE_DIMS.pop(instance, None)


def get_sunshine_pipewire_node(instance: int = 1) -> int | None:
    """Return the active PipeWire stream node id for an instance if set."""
    return _SUNSHINE_PIPEWIRE_NODES.get(instance)


def start_sunshine(
    instance: int = 1,
    pipewire_node: int | str | None = None,
    offset_x: int = 0,
    offset_y: int = 0,
    width: int | None = None,
    height: int | None = None,
    extra_environment: dict[str, str] | None = None,
) -> tuple[bool, str]:
    """Start isolated Sunshine engine binding child process to parent lifetime."""
    global _SUNSHINE_PROCESS, _SUNSHINE_PROCESSES
    ensure_sunshine_tray_disabled(instance)
    set_sunshine_pipewire_node(
        pipewire_node,
        instance,
        offset_x=offset_x,
        offset_y=offset_y,
        width=width,
        height=height,
    )

    if is_sunshine_running(instance):
        target_node = _SUNSHINE_PIPEWIRE_NODES.get(instance)
        running_proc = _SUNSHINE_PROCESSES.get(instance) or (_SUNSHINE_PROCESS if instance == 1 else None)
        needs_restart = False
        if running_proc and running_proc.pid:
            try:
                with open(f"/proc/{running_proc.pid}/environ", "rb") as f:
                    env_data = f.read().split(b"\0")
                requested_node = f"SUNSHINE_PIPEWIRE_NODE={target_node}".encode()
                needs_restart = (
                    requested_node not in env_data
                    if target_node
                    else any(entry.startswith(b"SUNSHINE_PIPEWIRE_NODE=") for entry in env_data)
                )
                for key, value in (extra_environment or {}).items():
                    requested_value = f"{key}={value}".encode()
                    if requested_value not in env_data:
                        needs_restart = True
                        break
            except OSError:
                needs_restart = True
        if needs_restart:
            stop_sunshine(instance, clear_pipewire_node=False)
        else:
            return True, f"Sunshine instance {instance} is already running."

    try:
        from monitorize.platform.gnome_virtual_monitor import map_sunshine_gnome_peripherals
        map_sunshine_gnome_peripherals()
    except Exception:
        pass

    candidates = get_sunshine_candidates(instance)
    if not candidates:
        return False, "Sunshine not found. Please verify Monitorize Sunshine is built or installed."

    config_dir = get_sunshine_config_dir(instance)
    config_home = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    profile_parent = os.path.join(config_home, "monitorize", f"sunshine-profile-{instance}")
    try:
        os.makedirs(profile_parent, exist_ok=True)
    except OSError:
        pass
    env = dict(os.environ)
    env.update({str(key): str(value) for key, value in (extra_environment or {}).items()})
    env["SUNSHINE_CONFIG_DIR"] = config_dir
    env["XDG_CONFIG_HOME"] = profile_parent

    node = _SUNSHINE_PIPEWIRE_NODES.get(instance)
    if node:
        env["SUNSHINE_PIPEWIRE_NODE"] = str(node)

    offset = _SUNSHINE_PIPEWIRE_OFFSETS.get(instance, (int(offset_x), int(offset_y)))
    env["SUNSHINE_PIPEWIRE_OFFSET_X"] = str(offset[0])
    env["SUNSHINE_PIPEWIRE_OFFSET_Y"] = str(offset[1])

    dims = _SUNSHINE_PIPEWIRE_DIMS.get(instance)
    if dims and dims[0] and dims[1]:
        env["SUNSHINE_PIPEWIRE_WIDTH"] = str(dims[0])
        env["SUNSHINE_PIPEWIRE_HEIGHT"] = str(dims[1])
    elif width and height:
        env["SUNSHINE_PIPEWIRE_WIDTH"] = str(int(width))
        env["SUNSHINE_PIPEWIRE_HEIGHT"] = str(int(height))

    errors = []
    for cmd in candidates:
        candidate_env = dict(env)
        assets_dir = get_sunshine_assets_dir(cmd[0])
        if assets_dir:
            candidate_env["SUNSHINE_ASSETS_DIR"] = assets_dir
        try:
            proc = subprocess.Popen(
                cmd,
                env=candidate_env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                preexec_fn=_set_pdeathsig,
            )
            _SUNSHINE_PROCESSES[instance] = proc
            if instance == 1:
                _SUNSHINE_PROCESS = proc
            time.sleep(0.35)
            exit_code = proc.poll()
            if exit_code is not None:
                _SUNSHINE_PROCESSES.pop(instance, None)
                if instance == 1:
                    _SUNSHINE_PROCESS = None
                detail = get_sunshine_last_error(instance)
                suffix = f": {detail}" if detail else ""
                errors.append(f"{cmd[0]} exited with code {exit_code}{suffix}")
                continue
            return True, f"Launched isolated Sunshine instance {instance} ({cmd[0]})."
        except (FileNotFoundError, OSError) as exc:
            errors.append(f"{cmd[0]}: {exc}")

    return False, f"Failed to start Sunshine instance {instance} ({'; '.join(errors)})"


def stop_sunshine(
    instance: int | None = None, clear_pipewire_node: bool = True
) -> tuple[bool, str]:
    """Gracefully stop Monitorize's Sunshine child process without affecting user's personal Sunshine."""
    global _SUNSHINE_PROCESS, _SUNSHINE_PROCESSES
    if instance is not None:
        instances_to_stop = [instance]
    else:
        instances_to_stop = list(_SUNSHINE_PROCESSES.keys())
        if _SUNSHINE_PROCESS is not None and 1 not in instances_to_stop:
            instances_to_stop.append(1)
        if not instances_to_stop:
            instances_to_stop = [1, 2]

    for inst in instances_to_stop:
        if clear_pipewire_node:
            set_sunshine_pipewire_node(None, inst)
        proc = _SUNSHINE_PROCESSES.pop(inst, None)
        if proc is None and inst == 1 and _SUNSHINE_PROCESS is not None:
            proc = _SUNSHINE_PROCESS
            _SUNSHINE_PROCESS = None
        if proc is not None:
            try:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        proc.kill()
            except Exception:
                pass
        clear_sunshine_output_name(inst)

    if instance is None or instance == 1:
        _SUNSHINE_PROCESS = None

    return True, "Sunshine stopped successfully."


atexit.register(stop_sunshine)


def open_sunshine_dashboard(path_or_instance: str | int = "", path: str = "", instance: int = 1) -> bool:
    """Open Sunshine Web UI in the default browser, auto-starting Sunshine if needed."""
    if isinstance(path_or_instance, int):
        target_instance = path_or_instance
        clean_path = path.strip("/")
    elif isinstance(path_or_instance, str):
        clean_path = path_or_instance.strip("/")
        target_instance = instance
    else:
        target_instance = instance
        clean_path = path.strip("/")

    if not is_sunshine_running(target_instance):
        start_sunshine(target_instance)

    url = get_sunshine_web_url(target_instance)
    if clean_path:
        url = f"{url}/{clean_path}"

    try:
        return webbrowser.open(url)
    except Exception:
        return False


def pair_moonlight_pin(pin: str, name: str = "Monitorize Display", instance: int | None = None) -> tuple[bool, str]:
    """Submit a 4-digit Moonlight pairing PIN to Sunshine's local API.

    Broadcasts to active Sunshine instances so pairing works effortlessly
    regardless of which virtual monitor instance is awaiting authentication.

    Returns:
        tuple[bool, str]: (success, status_message)
    """
    clean_pin = str(pin or "").strip()
    if not (len(clean_pin) == 4 and clean_pin.isdigit()):
        return False, "PIN must be exactly 4 digits."

    import json
    import ssl
    import urllib.request

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    payload = json.dumps({"pin": clean_pin, "name": name}).encode("utf-8")

    if instance is not None and str(instance).isdigit() and int(instance) in (1, 2):
        inst_num = int(instance)
        candidates = [inst_num] + [i for i in (1, 2) if i != inst_num]
    else:
        candidates = [1, 2]

    last_error = ""
    for inst in candidates:
        url = get_sunshine_web_url(inst)
        req = urllib.request.Request(
            f"{url}/api/pin",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, context=ctx, timeout=4.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("status") is True:
                    return True, "Paired successfully! Moonlight is now unlocked."
                else:
                    err = data.get("error", "")
                    if err:
                        last_error = err
        except urllib.error.HTTPError as exc:
            try:
                err_data = json.loads(exc.read().decode("utf-8"))
                last_error = err_data.get("error", f"Pairing error ({exc.code})")
            except Exception:
                last_error = f"Pairing failed with HTTP error {exc.code}."
        except Exception as exc:
            if not last_error:
                last_error = f"Could not connect to Sunshine API: {exc}"

    return False, last_error or "Pairing failed. Make sure Moonlight is asking for a PIN."



def sync_sunshine_stream_config(
    output_name: str,
    encoder: str = "Auto",
    codec: str = "Auto",
    native_pen_touch: bool = True,
    instance: int = 1,
    capture: str = "",
    adapter_name: str = "",
) -> tuple[bool, str]:
    """Atomically synchronize all active streaming parameters to sunshine.conf in a single pass."""
    clean_out = str(output_name or "").strip()

    clean_enc = str(encoder or "").strip()
    clean_capture = str(capture or "").strip()
    clean_adapter = str(adapter_name or "").strip()
    mapping = {
        "auto": "",
        "nvidia": "nvenc",
        "nvidia nvenc": "nvenc",
        "nvidia nvenc (nvh264enc)": "nvenc",
        "nvenc": "nvenc",
        "va-api": "vaapi",
        "vaapi": "vaapi",
        "intel/amd va-api (vah264enc)": "vaapi",
        "vulkan": "vulkan",
        "software": "software",
        "software enc": "software",
        "software (cpu)": "software",
        "software (cpu / x264enc)": "software",
    }
    target_encoder = mapping.get(clean_enc.lower(), clean_enc)

    clean_codec = str(codec or "").strip().lower()
    if "h.264" in clean_codec or "avc" in clean_codec or clean_codec == "h264":
        hevc_val, av1_val = 1, 1
    elif "h.265" in clean_codec or "hevc" in clean_codec or clean_codec == "h265":
        hevc_val, av1_val = 2, 1
    elif "av1" in clean_codec:
        hevc_val, av1_val = 1, 2
    else:
        hevc_val, av1_val = 0, 0

    pen_touch_val = "enabled" if native_pen_touch else "disabled"

    config_path = get_sunshine_config_path(instance)
    try:
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
    except OSError:
        pass

    lines = []
    found_output = False
    found_encoder = False
    found_hevc = False
    found_av1 = False
    found_tray = False
    found_pen_touch = False
    found_capture = False
    found_adapter = False
    prev_output = ""

    if os.path.isfile(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped.startswith("output_name"):
                        parts = stripped.split("=", 1)
                        if len(parts) == 2:
                            prev_output = parts[1].strip()
                        lines.append(f"output_name = {clean_out}\n")
                        found_output = True
                    elif stripped.startswith("encoder"):
                        lines.append(f"encoder = {target_encoder}\n")
                        found_encoder = True
                    elif stripped.startswith("hevc_mode"):
                        lines.append(f"hevc_mode = {hevc_val}\n")
                        found_hevc = True
                    elif stripped.startswith("av1_mode"):
                        lines.append(f"av1_mode = {av1_val}\n")
                        found_av1 = True
                    elif stripped.startswith("native_pen_touch"):
                        lines.append(f"native_pen_touch = {pen_touch_val}\n")
                        found_pen_touch = True
                    elif stripped.startswith("capture"):
                        lines.append(f"capture = {clean_capture}\n")
                        found_capture = True
                    elif stripped.startswith("adapter_name"):
                        lines.append(f"adapter_name = {clean_adapter}\n")
                        found_adapter = True
                    elif stripped.startswith("system_tray"):
                        lines.append("system_tray = disabled\n")
                        found_tray = True
                    else:
                        lines.append(line)
        except OSError:
            pass

    if not found_output:
        lines.append(f"output_name = {clean_out}\n")
    if not found_encoder:
        lines.append(f"encoder = {target_encoder}\n")
    if not found_hevc:
        lines.append(f"hevc_mode = {hevc_val}\n")
    if not found_av1:
        lines.append(f"av1_mode = {av1_val}\n")
    if not found_pen_touch:
        lines.append(f"native_pen_touch = {pen_touch_val}\n")
    if not found_capture:
        lines.append(f"capture = {clean_capture}\n")
    if not found_adapter:
        lines.append(f"adapter_name = {clean_adapter}\n")
    if not found_tray:
        lines.append("system_tray = disabled\n")

    try:
        with open(config_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
    except OSError as exc:
        return False, f"Could not write sunshine.conf: {exc}"

    if is_sunshine_running(instance):
        if prev_output and clean_out and prev_output != clean_out:
            restart_sunshine(instance)
        else:
            import json
            import ssl
            import urllib.request

            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            url = get_sunshine_web_url(instance)
            payload = json.dumps({
                "output_name": clean_out,
                "encoder": target_encoder,
                "hevc_mode": hevc_val,
                "av1_mode": av1_val,
                "native_pen_touch": pen_touch_val,
                "capture": clean_capture,
                "adapter_name": clean_adapter,
            }).encode("utf-8")
            req = urllib.request.Request(
                f"{url}/api/config",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, context=ctx, timeout=3.0) as _resp:
                    pass
            except Exception:
                pass

    return True, f"Synchronized Sunshine instance {instance} config"


def set_sunshine_output_name(output_name: str, instance: int = 1) -> tuple[bool, str]:
    """Configure Sunshine to capture a specific virtual display output name."""
    clean_name = str(output_name or "").strip()
    config_path = get_sunshine_config_path(instance)
    try:
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
    except OSError:
        pass

    lines = []
    found_output = False
    found_tray = False
    if os.path.isfile(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped.startswith("output_name"):
                        lines.append(f"output_name = {clean_name}\n")
                        found_output = True
                    elif stripped.startswith("system_tray"):
                        lines.append("system_tray = disabled\n")
                        found_tray = True
                    else:
                        lines.append(line)
        except OSError:
            pass

    if not found_output:
        lines.append(f"output_name = {clean_name}\n")
    if not found_tray:
        lines.append("system_tray = disabled\n")

    try:
        with open(config_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
    except OSError as exc:
        return False, f"Could not write sunshine.conf: {exc}"

    if is_sunshine_running(instance):
        import json
        import ssl
        import urllib.request

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        url = get_sunshine_web_url(instance)
        payload = json.dumps({"output_name": clean_name}).encode("utf-8")
        req = urllib.request.Request(
            f"{url}/api/config",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=3.0) as _resp:
                pass
        except Exception:
            pass

    return True, f"Configured Sunshine instance {instance} output_name = {clean_name}"


def restart_sunshine(instance: int = 1) -> tuple[bool, str]:
    """Request Sunshine to restart its process and stream capture via local REST API."""
    if not is_sunshine_running(instance):
        return start_sunshine(instance)

    import ssl
    import urllib.request

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    url = get_sunshine_web_url(instance)
    req = urllib.request.Request(
        f"{url}/api/restart",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=4.0) as _resp:
            return True, f"Sunshine instance {instance} restarted successfully."
    except Exception as exc:
        return False, f"Could not restart Sunshine instance {instance} via API: {exc}"


def clear_sunshine_output_name(instance: int = 1) -> tuple[bool, str]:
    """Reset Sunshine output_name configuration back to default."""
    return set_sunshine_output_name("", instance=instance)


def set_sunshine_encoder(encoder_name: str, instance: int = 1) -> tuple[bool, str]:
    """Configure Sunshine's forced video encoder option in sunshine.conf and REST API."""
    clean = str(encoder_name or "").strip()
    mapping = {
        "auto": "",
        "nvidia": "nvenc",
        "nvidia nvenc": "nvenc",
        "nvidia nvenc (nvh264enc)": "nvenc",
        "nvenc": "nvenc",
        "va-api": "vaapi",
        "vaapi": "vaapi",
        "intel/amd va-api (vah264enc)": "vaapi",
        "vulkan": "vulkan",
        "software": "software",
        "software enc": "software",
        "software (cpu)": "software",
        "software (cpu / x264enc)": "software",
    }
    target_value = mapping.get(clean.lower(), clean)

    config_path = get_sunshine_config_path(instance)
    try:
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
    except OSError:
        pass

    lines = []
    found = False
    if os.path.isfile(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped.startswith("encoder"):
                        lines.append(f"encoder = {target_value}\n")
                        found = True
                    else:
                        lines.append(line)
        except OSError:
            pass

    if not found:
        lines.append(f"encoder = {target_value}\n")

    try:
        with open(config_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
    except OSError as exc:
        return False, f"Could not write sunshine.conf: {exc}"

    if is_sunshine_running(instance):
        import json
        import ssl
        import urllib.request

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        url = get_sunshine_web_url(instance)
        payload = json.dumps({"encoder": target_value}).encode("utf-8")
        req = urllib.request.Request(
            f"{url}/api/config",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=3.0) as _resp:
                pass
        except Exception:
            pass

    return True, f"Sunshine instance {instance} encoder set to '{target_value or 'auto'}'."


def set_sunshine_codec(codec_name: str, instance: int = 1) -> tuple[bool, str]:
    """Configure Sunshine's forced video codec mode in sunshine.conf and REST API."""
    clean = str(codec_name or "").strip().lower()
    if "h.264" in clean or "avc" in clean or clean == "h264":
        hevc_val, av1_val, label = 1, 1, "H.264 (AVC)"
    elif "h.265" in clean or "hevc" in clean or clean == "h265":
        hevc_val, av1_val, label = 2, 1, "H.265 (HEVC)"
    elif "av1" in clean:
        hevc_val, av1_val, label = 1, 2, "AV1"
    else:
        hevc_val, av1_val, label = 0, 0, "Auto"

    config_path = get_sunshine_config_path(instance)
    try:
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
    except OSError:
        pass

    lines = []
    found_hevc = False
    found_av1 = False
    if os.path.isfile(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped.startswith("hevc_mode"):
                        lines.append(f"hevc_mode = {hevc_val}\n")
                        found_hevc = True
                    elif stripped.startswith("av1_mode"):
                        lines.append(f"av1_mode = {av1_val}\n")
                        found_av1 = True
                    else:
                        lines.append(line)
        except OSError:
            pass

    if not found_hevc:
        lines.append(f"hevc_mode = {hevc_val}\n")
    if not found_av1:
        lines.append(f"av1_mode = {av1_val}\n")

    try:
        with open(config_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
    except OSError as exc:
        return False, f"Could not write sunshine.conf: {exc}"

    if is_sunshine_running(instance):
        import json
        import ssl
        import urllib.request

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        url = get_sunshine_web_url(instance)
        payload = json.dumps({"hevc_mode": hevc_val, "av1_mode": av1_val}).encode("utf-8")
        req = urllib.request.Request(
            f"{url}/api/config",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=3.0) as _resp:
                pass
        except Exception:
            pass

    return True, f"Sunshine instance {instance} video codec set to '{label}'."


def set_sunshine_native_pen_touch(enabled: bool, instance: int = 1) -> tuple[bool, str]:
    """Configure native_pen_touch (enabled/disabled) in sunshine.conf and sync via REST API."""
    val_str = "enabled" if enabled else "disabled"
    config_path = get_sunshine_config_path(instance)
    try:
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
    except OSError:
        pass

    lines = []
    found = False
    if os.path.isfile(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped.startswith("native_pen_touch"):
                        lines.append(f"native_pen_touch = {val_str}\n")
                        found = True
                    else:
                        lines.append(line)
        except OSError:
            pass

    if not found:
        lines.append(f"native_pen_touch = {val_str}\n")

    try:
        with open(config_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
    except OSError as exc:
        return False, f"Could not write sunshine.conf: {exc}"

    if is_sunshine_running(instance):
        import json
        import ssl
        import urllib.request

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        url = get_sunshine_web_url(instance)
        payload = json.dumps({"native_pen_touch": val_str}).encode("utf-8")
        req = urllib.request.Request(
            f"{url}/api/config",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=3.0) as _resp:
                pass
        except Exception:
            pass

    status = "enabled" if enabled else "disabled"
    return True, f"Sunshine instance {instance} touch & stylus input set to '{status}'."



DEFAULT_SUNSHINE_CONFIG = {

    "locale": "en",
    "sunshine_name": "Monitorize Display",
    "min_log_level": "2",

    "controller": "enabled",
    "gamepad": "auto",
    "motion_as_ds4": "enabled",
    "touchpad_as_ds4": "enabled",
    "ds4_back_as_touchpad_click": "enabled",
    "ds5_inputtino_randomize_mac": "enabled",
    "back_button_timeout": "-1",
    "keyboard": "enabled",
    "key_repeat_delay": "500",
    "key_repeat_frequency": "24.9",
    "always_send_scancodes": "enabled",
    "key_rightalt_to_key_win": "disabled",
    "mouse": "enabled",
    "high_resolution_scrolling": "enabled",
    "native_pen_touch": "enabled",

    "audio_sink": "",
    "virtual_sink": "",
    "stream_audio": "enabled",
    "adapter_name": "",
    "output_name": "",
    "encoder": "",
    "max_bitrate": "0",
    "minimum_fps_target": "0",
    "dd_configuration_option": "disabled",
    "dd_resolution_option": "auto",
    "dd_manual_resolution": "",
    "dd_refresh_rate_option": "auto",
    "dd_manual_refresh_rate": "",
    "dd_hdr_option": "auto",

    "upnp": "disabled",
    "address_family": "both",
    "bind_address": "",
    "port": "47989",
    "origin_web_ui_allowed": "lan",
    "csrf_allowed_origins": "",
    "external_ip": "",
    "lan_encryption_mode": "0",
    "wan_encryption_mode": "1",
    "ping_timeout": "10000",
    "packetsize": "0",

    "file_apps": "",
    "credentials_file": "",
    "log_path": "",
    "pkey": "",
    "cert": "",
    "file_state": "",

    "fec_percentage": "20",
    "qp": "28",
    "min_threads": "2",
    "hevc_mode": "0",
    "av1_mode": "0",
    "capture": "",

    "nvenc_preset": "1",
    "nvenc_twopass": "quarter_res",
    "nvenc_spatial_aq": "disabled",
    "nvenc_vbv_increase": "0",
    "nvenc_realtime_hags": "enabled",
    "nvenc_split_encode": "driver_decides",
    "nvenc_latency_over_power": "enabled",
    "nvenc_h264_cavlc": "disabled",

    "amd_usage": "ultralowlatency",
    "amd_rc": "vbr_latency",
    "amd_enforce_hrd": "disabled",
    "amd_quality": "balanced",
    "amd_preanalysis": "disabled",
    "amd_vbaq": "enabled",
    "amd_coder": "auto",

    "qsv_preset": "medium",
    "qsv_coder": "auto",
    "qsv_slow_hevc": "disabled",

    "vaapi_rc": "auto",
    "vaapi_quality": "auto",
    "vaapi_strict_rc_buffer": "disabled",
    "vaapi_blbrc": "disabled",

    "vk_tune": "0",
    "vk_rc_mode": "0",

    "sw_preset": "superfast",
    "sw_tune": "zerolatency",
}


def get_sunshine_config(instance: int = 1) -> dict[str, str]:
    """Retrieve Sunshine configuration dictionary from REST API or local config file."""
    config = dict(DEFAULT_SUNSHINE_CONFIG)
    config["port"] = str(get_sunshine_port(instance))
    config["sunshine_name"] = get_sunshine_device_name(instance)


    config_path = get_sunshine_config_path(instance)
    if os.path.isfile(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    key = key.strip()
                    val = val.strip()
                    if key:
                        config[key] = val
        except OSError:
            pass


    if is_sunshine_running(instance):
        import json
        import ssl
        import urllib.request

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        url = get_sunshine_web_url(instance)
        req = urllib.request.Request(
            f"{url}/api/config",
            headers={"Accept": "application/json"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=3.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                for k, v in data.items():
                    if k not in ("status", "platform", "version") and v is not None:
                        config[k] = str(v)
        except Exception:
            pass

    return config


def save_sunshine_config(new_config: dict[str, str], instance: int = 1) -> tuple[bool, str]:
    """Save configuration dictionary to sunshine.conf and push to running Sunshine instance."""
    config_path = get_sunshine_config_path(instance)
    try:
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
    except OSError:
        pass

    current_config = get_sunshine_config(instance)
    current_config.update({str(k): str(v) for k, v in new_config.items() if v is not None})


    lines = [
        "# Sunshine configuration generated by Monitorize\n",
    ]
    for k, v in sorted(current_config.items()):
        if str(v).strip():
            lines.append(f"{k} = {v}\n")

    try:
        with open(config_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
    except OSError as exc:
        return False, f"Could not write sunshine.conf: {exc}"


    if is_sunshine_running(instance):
        import json
        import ssl
        import urllib.request

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        url = get_sunshine_web_url(instance)
        payload = json.dumps(current_config).encode("utf-8")
        req = urllib.request.Request(
            f"{url}/api/config",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=3.0) as _resp:
                pass
        except Exception:
            pass

        restart_sunshine(instance)

    return True, f"Sunshine instance {instance} settings saved and applied successfully."
