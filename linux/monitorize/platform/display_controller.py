"""Compositor-specific virtual output management."""

import json
import os
import re
import subprocess
import time


MANAGED_VIRTUAL_OUTPUT_PATTERN = re.compile(r"^(?:HEADLESS|Monitorize)-\d+$")


class DisplayController:
    def __init__(self, de):
        self.de = de
        self.created_output = None
        self.additional_output = None
        self._hyprctl_instance = None
        self._reported_hyprctl_failures = set()
        self._hyprland_diagnostics_logged = False

    @staticmethod
    def _host_command(*args):
        command = list(map(str, args))
        if os.path.isfile("/.flatpak-info"):
            return ["flatpak-spawn", "--host", "--directory=/", *command]
        return command

    @staticmethod
    def _hyprctl_command(*args, instance=None):
        command = ["hyprctl", *map(str, args)]
        if instance is not None:
            command[1:1] = ["-i", str(instance)]
        
        
        return DisplayController._host_command(*command)

    def _run_hyprctl(self, *args, instance=None):
        """Run the host-matching hyprctl when Monitorize is sandboxed."""
        command = self._hyprctl_command(
            *args,
            instance=self._hyprctl_instance if instance is None else instance,
        )
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            self._log_hyprctl_failure(command, result=result)
        return result

    def _log_hyprctl_failure(self, command, result=None, exc=None):
        """Log each command failure once, including enough IPC context to debug it."""
        if result is not None:
            detail = (
                f"returncode={result.returncode} "
                f"stdout={str(result.stdout or '').strip()!r} "
                f"stderr={str(result.stderr or '').strip()!r}"
            )
        else:
            detail = f"error={str(exc or '').strip()!r}"
        key = (tuple(command), detail)
        if key not in self._reported_hyprctl_failures:
            self._reported_hyprctl_failures.add(key)
            print(f"[hyprland] `{' '.join(command)}` failed: {detail}", flush=True)

    @staticmethod
    def _command_error(action, result=None, exc=None):
        detail = ""
        if result is not None:
            output = str(result.stderr or result.stdout or "").strip()
            detail = f"returncode={result.returncode}"
            if output:
                detail += f": {output}"
        elif exc is not None:
            detail = str(exc).strip()
        message = f"Hyprland could not {action}"
        return f"{message}: {detail}" if detail else message

    def _monitor_json(self):
        try:
            result = self._run_hyprctl("-j", "monitors", "all")
            if result.returncode == 0:
                return json.loads(result.stdout)
        except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError) as exc:
            self._log_hyprctl_failure(
                self._hyprctl_command("-j", "monitors", "all"), exc=exc
            )
        return None

    def headless_monitors(self):
        monitors = self._monitor_json()
        if monitors is not None:
            return [
                item.get("name") for item in monitors
                if MANAGED_VIRTUAL_OUTPUT_PATTERN.fullmatch(
                    str(item.get("name", ""))
                )
            ]
        try:
            result = self._run_hyprctl("monitors", "all")
            if result.returncode == 0:
                return sorted(set(re.findall(
                    r"(?<![\w-])(?:HEADLESS|Monitorize)-\d+(?![\w-])",
                    result.stdout,
                )))
        except (OSError, subprocess.SubprocessError) as exc:
            self._log_hyprctl_failure(
                self._hyprctl_command("monitors", "all"), exc=exc
            )
        return []

    def _hyprland_instances(self):
        """Return Hyprland instance selectors reported by the host hyprctl."""
        try:
            result = self._run_hyprctl("-j", "instances")
        except (OSError, subprocess.SubprocessError) as exc:
            self._log_hyprctl_failure(
                self._hyprctl_command("-j", "instances"), exc=exc
            )
            return []
        if result.returncode != 0:
            return []
        try:
            instances = json.loads(result.stdout)
            return [str(item["instance"]) for item in instances if item.get("instance")]
        except (ValueError, json.JSONDecodeError, TypeError, KeyError) as exc:
            self._log_hyprctl_failure(
                self._hyprctl_command("-j", "instances"), exc=exc
            )
            return []

    def _verify_hyprland_ipc(self):
        """Prove the selected hyprctl can reach one active compositor instance."""
        if self._hyprland_diagnostics_logged:
            return ""
        self._hyprland_diagnostics_logged = True
        mode = "host-via-flatpak-spawn" if os.path.isfile("/.flatpak-info") else "native"
        print(f"[hyprland] execution mode={mode}", flush=True)
        try:
            version = self._run_hyprctl("version")
        except (OSError, subprocess.SubprocessError) as exc:
            self._log_hyprctl_failure(self._hyprctl_command("version"), exc=exc)
            return self._command_error(
                "launch host hyprctl" if os.path.isfile("/.flatpak-info") else "launch hyprctl",
                exc=exc,
            )
        if version.returncode == 0:
            print(f"[hyprland] version={version.stdout.strip()}", flush=True)
        try:
            status = self._run_hyprctl("status")
            if status.returncode == 0:
                print(f"[hyprland] status={status.stdout.strip()}", flush=True)
        except (OSError, subprocess.SubprocessError) as exc:
            self._log_hyprctl_failure(self._hyprctl_command("status"), exc=exc)

        instances = self._hyprland_instances()
        if instances:
            print(f"[hyprland] instances={', '.join(instances)}", flush=True)

        try:
            monitors = self._run_hyprctl("-j", "monitors", "all")
        except (OSError, subprocess.SubprocessError) as exc:
            self._log_hyprctl_failure(
                self._hyprctl_command("-j", "monitors", "all"), exc=exc
            )
            return self._command_error("connect to the active Hyprland instance", exc=exc)
        if monitors.returncode == 0:
            return ""
        if len(instances) != 1:
            if len(instances) > 1:
                return (
                    "Hyprland could not connect to the active instance; "
                    "multiple instances were found, so Monitorize will not choose one"
                )
            return self._command_error(
                "connect to the active Hyprland instance", result=monitors
            )

        self._hyprctl_instance = instances[0]
        print(
            f"[hyprland] normal IPC failed; retrying with instance "
            f"{self._hyprctl_instance}",
            flush=True,
        )
        try:
            monitors = self._run_hyprctl("-j", "monitors", "all")
        except (OSError, subprocess.SubprocessError) as exc:
            self._log_hyprctl_failure(
                self._hyprctl_command(
                    "-j", "monitors", "all", instance=self._hyprctl_instance
                ),
                exc=exc,
            )
            return self._command_error("connect to the selected Hyprland instance", exc=exc)
        if monitors.returncode == 0:
            print(
                f"[hyprland] using Hyprland instance {self._hyprctl_instance}",
                flush=True,
            )
            return ""
        return self._command_error(
            "connect to the selected Hyprland instance", result=monitors
        )

    def prepare_hyprland(self, width, height, fps, slot="primary"):
        error = self._verify_hyprland_ipc()
        if error:
            return "", error
        output = "Monitorize-2" if slot == "additional" else "Monitorize-1"
        if output in self.headless_monitors():
            return "", (
                f"Hyprland output {output} already exists; remove stagnant "
                "virtual displays and try again"
            )
        try:
            result = self._run_hyprctl("output", "create", "headless", output)
        except (OSError, subprocess.SubprocessError) as exc:
            return "", self._command_error(
                "launch host hyprctl" if os.path.isfile("/.flatpak-info")
                else "launch hyprctl",
                exc=exc,
            )
        if result.returncode != 0:
            return "", self._command_error(
                f"create the headless output {output}", result=result
            )
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if output in self.headless_monitors():
                break
            time.sleep(0.1)
        else:
            return "", f"Hyprland did not expose the new output {output}"
        mode = f"{width}x{height}@{fps}"
        try:
            configured = self._run_hyprctl(
                "eval",
                f"hl.monitor({{ output = '{output}', mode = '{mode}', "
                "position = 'auto', scale = 1.0, disabled = false })",
            )
        except (OSError, subprocess.SubprocessError) as exc:
            self._remove_hyprland_output(output)
            return "", self._command_error(f"configure {output} with Lua", exc=exc)
        if configured.returncode != 0:
            lua_error = self._command_error(f"configure {output} with Lua", result=configured)
            try:
                configured = self._run_hyprctl(
                    "keyword", "monitor", f"{output},{mode},auto,1"
                )
            except (OSError, subprocess.SubprocessError) as exc:
                self._remove_hyprland_output(output)
                legacy_error = self._command_error(
                    f"configure {output} with legacy monitor syntax", exc=exc
                )
                return "", f"{lua_error}; {legacy_error}"
            if configured.returncode != 0:
                self._remove_hyprland_output(output)
                legacy_error = self._command_error(
                    f"configure {output} with legacy monitor syntax", result=configured
                )
                return "", f"{lua_error}; {legacy_error}"
            print("[hyprland] Lua monitor configuration was rejected; using legacy syntax", flush=True)
        if not self.wait_for_headless_ready(output, width, height, fps=fps):
            actual = self._monitor_details(output)
            self._remove_hyprland_output(output)
            actual_mode = (
                f"{actual.get('width')}x{actual.get('height')}"
                if actual else "not present"
            )
            return "", (
                f"Hyprland created {output} but expected {width}x{height}@{fps}Hz; "
                f"actual {actual_mode}"
            )
        if slot == "additional":
            self.additional_output = output
        else:
            self.created_output = output
        return output, ""

    def _remove_hyprland_output(self, output):
        try:
            result = self._run_hyprctl("output", "remove", output)
            return result.returncode == 0
        except (OSError, subprocess.SubprocessError) as exc:
            self._log_hyprctl_failure(
                self._hyprctl_command("output", "remove", output), exc=exc
            )
            return False

    def remove_hyprland_output(self, slot="primary"):
        output = self.additional_output if slot == "additional" else self.created_output
        if not output or self.de != "hyprland":
            return
        self._remove_hyprland_output(output)
        if slot == "additional":
            self.additional_output = None
        else:
            self.created_output = None

    def launch_host_display_settings(self):
        """Launch host nwg-displays from a Flatpak compositor session.

        The compositor performs the final launch so the GUI inherits the real
        host Wayland, IPC, PATH, and XDG configuration environment.

        Returns an empty string on success, otherwise a user-facing error.
        """
        if not os.path.isfile("/.flatpak-info"):
            return "Host display-settings launch is only needed inside Flatpak"
        try:
            probe = subprocess.run(
                self._host_command("nwg-displays", "--version"),
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return "nwg-displays is not installed on the host"
        if probe.returncode != 0:
            return "nwg-displays is not installed on the host"

        try:
            if self.de == "hyprland":
                ipc_error = self._verify_hyprland_ipc()
                if ipc_error:
                    return ipc_error
                
                
                result = self._run_hyprctl(
                    "dispatch", 'hl.dsp.exec_cmd("nwg-displays")'
                )
                if result.returncode != 0:
                    result = self._run_hyprctl(
                        "dispatch", "exec", "nwg-displays"
                    )
            elif self.de == "sway":
                result = self._run_swaymsg("exec", "nwg-displays")
            else:
                return "Display settings are only available on Hyprland and Sway"
        except (OSError, subprocess.SubprocessError) as exc:
            return f"Failed to launch nwg-displays: {exc}"
        if result.returncode != 0:
            detail = str(result.stderr or result.stdout or "").strip()
            return (
                f"Failed to launch nwg-displays: {detail}"
                if detail else "Failed to launch nwg-displays"
            )
        return ""

    @staticmethod
    def _swaymsg_command(*args):
        return DisplayController._host_command("swaymsg", *args)

    def _run_swaymsg(self, *args, timeout=5):
        """Run the host Sway IPC client when Monitorize is sandboxed."""
        return subprocess.run(
            self._swaymsg_command(*args),
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def sway_version_supported(self):
        """Return whether the running Sway can remove virtual outputs."""
        try:
            result = self._run_swaymsg("-t", "get_version", "-r", timeout=2)
            if result.returncode != 0:
                return False
            version = json.loads(result.stdout).get("human_readable", "")
            match = re.search(r"(\d+)\.(\d+)", version)
            return bool(match and tuple(map(int, match.groups())) >= (1, 8))
        except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError):
            return False

    def sway_outputs(self):
        try:
            result = self._run_swaymsg("-t", "get_outputs", "-r", timeout=2)
            if result.returncode == 0:
                return json.loads(result.stdout)
        except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError):
            pass
        return []

    @staticmethod
    def _sway_headless_outputs(outputs):
        return {
            str(output.get("name", "")) for output in outputs
            if MANAGED_VIRTUAL_OUTPUT_PATTERN.fullmatch(
                str(output.get("name", ""))
            )
        }

    @staticmethod
    def _sway_right_edge(outputs):
        edges = []
        for output in outputs:
            if not output.get("active"):
                continue
            rect = output.get("rect", {})
            edges.append(int(rect.get("x", 0)) + int(rect.get("width", 0)))
        return max(edges, default=0)

    def _wait_for_new_sway_output(self, old, timeout_s=2.0):
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            outputs = self.sway_outputs()
            created = sorted(self._sway_headless_outputs(outputs) - old)
            if len(created) == 1:
                return created[0], outputs
            time.sleep(0.1)
        return "", []

    def _wait_for_sway_output_ready(self, output_name, width, height,
                                    timeout_s=2.0, poll_interval_s=0.1):
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            for output in self.sway_outputs():
                mode = output.get("current_mode") or {}
                if (output.get("name") == output_name and output.get("active")
                        and mode.get("width") == width and mode.get("height") == height):
                    return True
            time.sleep(poll_interval_s)
        return False

    def prepare_sway(self, width, height, fps, slot="primary"):
        if not self.sway_version_supported():
            return "", "Sway 1.8 or newer is required for virtual outputs"

        existing_outputs = self.sway_outputs()
        old = self._sway_headless_outputs(existing_outputs)
        try:
            result = self._run_swaymsg("create_output")
        except (OSError, subprocess.SubprocessError):
            return "", "Sway could not create a virtual output"
        if result.returncode != 0:
            return "", "Sway could not create a virtual output"

        output, observed_outputs = self._wait_for_new_sway_output(old)
        if not output:
            return "", "Sway did not expose one new virtual output"

        mode = f"{width}x{height}@{fps}Hz"
        configured = self._run_swaymsg(
            "output", output, "mode", "--custom", mode,
            "pos", str(self._sway_right_edge(observed_outputs)), "0", "scale", "1",
        )
        if configured.returncode != 0:
            self._run_swaymsg("output", output, "unplug")
            return "", f"Sway could not configure {output}"
        if not self._wait_for_sway_output_ready(output, width, height):
            self._run_swaymsg("output", output, "unplug")
            return "", f"Sway did not activate {output} at the requested resolution"

        if slot == "additional":
            self.additional_output = output
        else:
            self.created_output = output
        return output, ""

    def remove_sway_output(self, slot="primary"):
        output = self.additional_output if slot == "additional" else self.created_output
        if not output or self.de != "sway":
            return
        try:
            self._run_swaymsg("output", output, "unplug")
        except (OSError, subprocess.SubprocessError):
            pass
        if slot == "additional":
            self.additional_output = None
        else:
            self.created_output = None

    def remove_stagnant_virtual_displays(self):
        """Remove exact HEADLESS-<number> and Monitorize-<number> outputs."""
        removed = 0
        if self.de == "hyprland":
            if self._verify_hyprland_ipc():
                return 0
            outputs = sorted(set(self.headless_monitors()))
            for output in outputs:
                if (MANAGED_VIRTUAL_OUTPUT_PATTERN.fullmatch(output)
                        and self._remove_hyprland_output(output)):
                    removed += 1
            return removed

        if self.de == "sway":
            outputs = sorted(self._sway_headless_outputs(self.sway_outputs()))
            for output in outputs:
                try:
                    result = self._run_swaymsg("output", output, "unplug")
                except (OSError, subprocess.SubprocessError):
                    continue
                if result.returncode == 0:
                    removed += 1
        return removed

    def _monitor_details(self, output_name):
        """Return the current JSON state for one Hyprland output, if available."""
        monitors = self._monitor_json()
        if monitors is None:
            return None
        return next(
            (monitor for monitor in monitors if monitor.get("name") == output_name),
            None,
        )

    def wait_for_headless_ready(self, output_name, width, height, fps=None,
                                timeout_s=2.0, poll_interval_s=0.1):
        """Poll hyprctl until *output_name* appears with the expected resolution.

        Returns True if the output was detected with the correct mode before
        *timeout_s* elapsed, False otherwise.
        """
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            mon = self._monitor_details(output_name)
            if mon is not None:
                mode_matches = (
                    mon.get("width", 0) == width
                    and mon.get("height", 0) == height
                )
                refresh = mon.get("refreshRate")
                refresh_matches = (
                    fps is None
                    or (
                        refresh is not None
                        and abs(float(refresh) - float(fps)) <= 0.75
                    )
                )
                if mode_matches and refresh_matches:
                    return True
            time.sleep(poll_interval_s)
        return False

    def cleanup(self):
        if self.de == "sway":
            self.remove_sway_output("additional")
            self.remove_sway_output("primary")
            return
        self.remove_hyprland_output("additional")
        self.remove_hyprland_output("primary")
