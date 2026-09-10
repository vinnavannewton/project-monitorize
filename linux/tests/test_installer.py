import unittest
from pathlib import Path
import re
import os
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[2]


class SunshineOnlyPackagingTest(unittest.TestCase):
    def test_flatpak_uses_ffmpeg_9_build_deps_bundle(self):
        manifest = (ROOT / "packaging/flatpak/com.vinnavan.Monitorize.yml").read_text()
        ffmpeg_module = (ROOT / "packaging/flatpak/modules/ffmpeg.json").read_text()
        self.assertIn("modules/ffmpeg.json", manifest)
        self.assertNotIn(
            "external/sunshine/packaging/linux/flatpak/modules/ffmpeg.json",
            manifest,
        )
        self.assertIn("v2026.905.170812", ffmpeg_module)
        self.assertIn(
            "880f0b9983ea9b55a6cceb2e5afa6388e256751f3cac2baf4ef0cf6eedc57aea",
            ffmpeg_module,
        )
        self.assertIn(
            "096069f2737a93ff44ba6445b700213708ab1fec01fd015e528a47845b5fde46",
            ffmpeg_module,
        )

    def test_forced_codec_participates_in_moonlight_negotiation(self):
        patch_text = (ROOT / "packaging/sunshine-strict-selection.patch").read_text()
        self.assertIn("force_non_h264 ? 0 : SCM_H264", patch_text)
        self.assertIn(
            "!force_non_h264 && video::last_encoder_probe_supported_yuv444_for_codec[0]",
            patch_text,
        )
        self.assertIn("MONITORIZE_STRICT_CODEC_REJECTED", patch_text)
        self.assertIn(
            "config::video.hevc_mode == 1 && config::video.av1_mode == 1",
            patch_text,
        )

    @staticmethod
    def write_executable(path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        path.chmod(0o755)

    @staticmethod
    def run_installer_prelude(body, *, env=None, stdin=None):
        script = (ROOT / "linux/scripts/install.sh").read_text()
        prelude = script.split("# ── Uninstall", 1)[0]
        return subprocess.run(
            ["bash", "-c", f"{prelude}\n{body}"],
            cwd=ROOT,
            env=env,
            stdin=stdin,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_missing_optional_ccache_does_not_abort_build_tool_setup(self):
        result = self.run_installer_prelude(
            r'''
command() {
    if [[ "$1" == "-v" && "$2" == "ccache" ]]; then
        return 1
    fi
    builtin command "$@"
}
SUNSHINE_BUILD_DIR="/nonexistent/monitorize-test-build"
configure_sunshine_build_tools
printf 'ccache=%s\n' "${SUNSHINE_CCACHE}"
'''
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ccache=off", result.stdout)

    def test_supported_node_versions_match_bundled_frontend_engine(self):
        result = self.run_installer_prelude(
            r'''
node_version_supported 20.19.0
! node_version_supported 21.7.0
! node_version_supported 22.11.0
node_version_supported 22.12.0
'''
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_cuda_auto_disables_without_nvidia_hardware(self):
        result = self.run_installer_prelude(
            r'''
CUDA_POLICY="auto"
SUNSHINE_CC="/usr/bin/gcc"
nvidia_gpu_present() { return 1; }
probe_cuda_toolchain() { echo "probe must not run" >&2; return 99; }
configure_sunshine_cuda
printf 'enabled=%s\nflags=%s\n' "${SUNSHINE_CUDA_ENABLED}" "${SUNSHINE_CUDA_CMAKE_FLAGS[*]}"
'''
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("enabled=off", result.stdout)
        self.assertIn("-DSUNSHINE_ENABLE_CUDA=OFF", result.stdout)
        self.assertNotIn("probe must not run", result.stderr)

    def test_nvidia_detection_uses_display_class_pci_devices_without_lspci(self):
        with tempfile.TemporaryDirectory() as tmp:
            pci_root = Path(tmp) / "pci"
            nvidia = pci_root / "0000:01:00.0"
            nvidia.mkdir(parents=True)
            (nvidia / "vendor").write_text("0x10de\n")
            (nvidia / "class").write_text("0x030200\n")
            env = os.environ.copy()
            env["MONITORIZE_PCI_SYSFS_ROOT"] = str(pci_root)
            result = self.run_installer_prelude(
                r'''
command() {
    if [[ "${1:-}" == "-v" && "${2:-}" == "nvidia-smi" ]]; then
        return 1
    fi
    builtin command "$@"
}
nvidia_gpu_present
''',
                env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_cuda_auto_enables_only_after_successful_toolchain_probe(self):
        result = self.run_installer_prelude(
            r'''
CUDA_POLICY="auto"
SUNSHINE_CC="/usr/bin/gcc-14"
nvidia_gpu_present() { return 0; }
probe_cuda_toolchain() {
    SUNSHINE_CUDA_COMPILER="/opt/cuda/bin/nvcc"
    SUNSHINE_CUDA_VERSION="12.8"
    return 0
}
configure_sunshine_cuda
printf 'enabled=%s\ncompiler=%s\nflags=%s\n' \
    "${SUNSHINE_CUDA_ENABLED}" "${SUNSHINE_CUDA_COMPILER}" "${SUNSHINE_CUDA_CMAKE_FLAGS[*]}"
'''
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("enabled=on", result.stdout)
        self.assertIn("compiler=/opt/cuda/bin/nvcc", result.stdout)
        self.assertIn("-DSUNSHINE_ENABLE_CUDA=ON", result.stdout)
        self.assertIn("-DCUDA_FAIL_ON_MISSING=ON", result.stdout)

    def test_cuda_probe_uses_selected_host_compiler_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp)
            fake_bin = fixture / "bin"
            nvcc = fake_bin / "nvcc"
            self.write_executable(nvcc, "#!/usr/bin/env bash\nexit 0\n")
            self.write_executable(
                fake_bin / "cmake",
                '''#!/usr/bin/env bash
printf '%s\n' "$*" > "${MONITORIZE_TEST_CMAKE_LOG}"
build_dir=""
while (( $# > 0 )); do
    if [[ "$1" == "-B" ]]; then
        build_dir="$2"
        shift 2
    else
        shift
    fi
done
mkdir -p "${build_dir}"
printf '12.8\n' > "${build_dir}/cuda-version.txt"
printf 'CMAKE_CUDA_COMPILER:FILEPATH=%s\n' "${CUDACXX}" > "${build_dir}/CMakeCache.txt"
''',
            )
            probe_tmp = fixture / "tmp"
            probe_tmp.mkdir()
            cmake_log = fixture / "cmake.log"
            env = os.environ.copy()
            env.update({
                "CUDACXX": str(nvcc),
                "MONITORIZE_TEST_CMAKE_LOG": str(cmake_log),
                "PATH": f"{fake_bin}:/usr/bin:/bin",
                "TMPDIR": str(probe_tmp),
            })
            result = self.run_installer_prelude(
                r'''
SUNSHINE_CC="/usr/bin/gcc-14"
probe_cuda_toolchain
printf 'compiler=%s\nversion=%s\n' "${SUNSHINE_CUDA_COMPILER}" "${SUNSHINE_CUDA_VERSION}"
''',
                env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f"compiler={nvcc}", result.stdout)
            self.assertIn("version=12.8", result.stdout)
            probe_args = cmake_log.read_text()
            self.assertIn("-DCMAKE_CUDA_HOST_COMPILER=/usr/bin/gcc-14", probe_args)
            self.assertIn(f"-DCMAKE_CUDA_COMPILER={nvcc}", probe_args)
            self.assertEqual(list(probe_tmp.iterdir()), [])

    def test_cuda_auto_falls_back_when_gpu_has_no_usable_toolchain(self):
        result = self.run_installer_prelude(
            r'''
CUDA_POLICY="auto"
SUNSHINE_CC="/usr/bin/gcc-14"
nvidia_gpu_present() { return 0; }
probe_cuda_toolchain() {
    SUNSHINE_CUDA_PROBE_ERROR="unsupported host compiler"
    return 1
}
configure_sunshine_cuda
printf 'enabled=%s\nflags=%s\n' "${SUNSHINE_CUDA_ENABLED}" "${SUNSHINE_CUDA_CMAKE_FLAGS[*]}"
'''
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("enabled=off", result.stdout)
        self.assertIn("-DSUNSHINE_ENABLE_CUDA=OFF", result.stdout)
        self.assertIn("toolchain is unusable", result.stderr)

    def test_cuda_on_is_strict_when_toolchain_probe_fails(self):
        result = self.run_installer_prelude(
            r'''
CUDA_POLICY="on"
SUNSHINE_CC="/usr/bin/gcc-14"
probe_cuda_toolchain() {
    SUNSHINE_CUDA_PROBE_ERROR="nvcc rejected gcc-14"
    return 1
}
configure_sunshine_cuda
'''
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--cuda=on requires a working CUDA 12+ compiler", result.stderr)
        self.assertIn("nvcc rejected gcc-14", result.stderr)

    def test_installer_paths_follow_xdg_base_directories(self):
        env = os.environ.copy()
        env.update({
            "HOME": "/tmp/monitorize-home",
            "XDG_CONFIG_HOME": "/tmp/monitorize-config",
            "XDG_DATA_HOME": "/tmp/monitorize-data",
        })
        result = self.run_installer_prelude(
            r'''
printf '%s\n' "${CONFIG_HOME}" "${DESKTOP_DIR}" "${ICON_DIR}"
''',
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            [
                "/tmp/monitorize-config",
                "/tmp/monitorize-data/applications",
                "/tmp/monitorize-data/icons/hicolor/192x192/apps",
            ],
        )

    def test_desktop_exec_quote_escapes_reserved_path_characters(self):
        value = '/tmp/a\\b$c`d%e"f'
        env = os.environ.copy()
        env["MONITORIZE_TEST_EXEC_PATH"] = value
        result = self.run_installer_prelude(
            "desktop_quote \"${MONITORIZE_TEST_EXEC_PATH}\"",
            env=env,
        )
        expected = value.replace("\\", "\\" * 4)
        for character in ('"', "`", "$"):
            expected = expected.replace(character, "\\" * 2 + character)
        expected = expected.replace("%", "%%")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, f'"{expected}"')

        string_result = self.run_installer_prelude(
            "desktop_string_escape \"${MONITORIZE_TEST_EXEC_PATH}\"",
            env=env,
        )
        self.assertEqual(string_result.returncode, 0, string_result.stderr)
        self.assertEqual(string_result.stdout, value.replace("\\", "\\\\"))

    def test_partial_preflight_does_not_require_git_or_sunshine_tools(self):
        script = (ROOT / "linux/scripts/install.sh").read_text()
        lines = script.splitlines()
        start = next(i for i, line in enumerate(lines) if "Pre-flight checks" in line) + 1
        end = next(i for i, line in enumerate(lines) if "Setup Virtual Environment" in line)
        preflight = "\n".join(lines[start:end])
        result = self.run_installer_prelude(
            f'''ICON_SRC={str(ROOT / "linux/monitorize/assets/monitorize_desktop_logo.png")!r}
PROJECT_DIR={str(ROOT / "linux")!r}
INSTALL_MODE="partial"
''' + r'''
command() {
    if [[ "${1:-}" == "-v" && "${2:-}" =~ ^(git|cmake|node|npm|patch|cksum)$ ]]; then
        return 1
    fi
    builtin command "$@"
}
''' + preflight
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_node_modules_preflight_detects_nonwritable_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            node_modules = Path(tmp) / "node_modules"
            node_modules.mkdir()
            blocked = node_modules / "root-owned-cache-file"
            blocked.touch(mode=0o444)
            result = self.run_installer_prelude(
                f'''SUNSHINE_SUBMODULE_DIR={str(Path(tmp))!r}
check_sunshine_node_modules_permissions
'''
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(str(blocked), result.stderr)

    def test_install_menu_explains_closed_input(self):
        result = self.run_installer_prelude(
            "select_install_mode",
            stdin=subprocess.DEVNULL,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no input was available", result.stderr)

    def test_installer_help_and_conflicting_modes(self):
        script = ROOT / "linux/scripts/install.sh"
        help_result = subprocess.run(
            [script, "--help"], text=True, capture_output=True, check=False
        )
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("--complete", help_result.stdout)
        self.assertIn("--partial", help_result.stdout)
        self.assertIn("--cuda=POLICY", help_result.stdout)
        self.assertIn("MONITORIZE_CUDA=auto|on|off", help_result.stdout)

        conflict = subprocess.run(
            [script, "--complete", "--partial"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(conflict.returncode, 2)
        self.assertIn("cannot be used together", conflict.stderr)

        cuda_partial_conflict = subprocess.run(
            [script, "--partial", "--cuda=off"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(cuda_partial_conflict.returncode, 2)
        self.assertIn("cannot be used together", cuda_partial_conflict.stderr)

        spaced_cuda_conflict = subprocess.run(
            [script, "--partial", "--cuda", "auto"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(spaced_cuda_conflict.returncode, 2)
        self.assertIn("cannot be used together", spaced_cuda_conflict.stderr)

        invalid_cuda = subprocess.run(
            [script, "--cuda=maybe"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(invalid_cuda.returncode, 2)
        self.assertIn("expected auto, on, or off", invalid_cuda.stderr)

    def test_complete_install_reaches_build_without_ccache_and_with_vulkan_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp)
            repository = fixture / "repository $cash`tick%field\\slash"
            linux = repository / "linux"
            script = linux / "scripts" / "install.sh"
            script.parent.mkdir(parents=True)
            shutil.copy2(ROOT / "linux/scripts/install.sh", script)

            (linux / "monitorize/assets").mkdir(parents=True)
            (linux / "monitorize/assets/monitorize_desktop_logo.png").write_bytes(b"png")
            (linux / "requirements.txt").write_text("")
            self.write_executable(
                linux / "native/kde_virtual_output/build.sh",
                '#!/usr/bin/env bash\nmkdir -p "$(dirname "$1")"\ncp /bin/true "$1"\n',
            )

            sunshine = repository / "external/sunshine"
            for relative in (
                "CMakeLists.txt",
                "package.json",
                "package-lock.json",
                "third-party/moonlight-common-c/CMakeLists.txt",
            ):
                path = sunshine / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            (sunshine / "src").mkdir()
            (sunshine / "src/video.cpp").write_text("MONITORIZE_STRICT_SELECTION_FAILED\n")
            (sunshine / "src/platform/linux").mkdir(parents=True)
            (sunshine / "src/platform/linux/portalgrab.cpp").write_text(
                "SUNSHINE_PORTAL_TOKEN_SCOPE\n"
            )
            (repository / "packaging").mkdir()
            (repository / "packaging/sunshine-strict-selection.patch").write_text("test patch\n")
            (repository / "packaging/sunshine-portal-token-scope.patch").write_text(
                "test portal patch\n"
            )

            fake_bin = fixture / "bin"
            self.write_executable(
                fake_bin / "python3",
                '''#!/usr/bin/env bash
if [[ "${1:-}" == "-c" ]]; then
    [[ "${2:-}" == *"sys.version_info"* ]] && printf '3.12.0\\n'
    exit 0
fi
if [[ "${1:-}" == "-m" && "${2:-}" == "venv" ]]; then
    target="${@: -1}"
    mkdir -p "${target}/bin"
    cp "$0" "${target}/bin/python3"
    ln -s /bin/true "${target}/bin/pip"
    exit 0
fi
exit 0
''',
            )
            self.write_executable(
                fake_bin / "node",
                '#!/usr/bin/env bash\nprintf \'22.12.0\\n\'\n',
            )
            self.write_executable(
                fake_bin / "git",
                '''#!/usr/bin/env bash
if [[ " $* " == *" rev-parse "* ]]; then
    printf '1111111111111111111111111111111111111111\\n'
fi
exit 0
''',
            )
            self.write_executable(
                fake_bin / "cmake",
                '''#!/usr/bin/env bash
printf '%s\n' "$*" >> "${MONITORIZE_TEST_CMAKE_LOG}"
if [[ "${1:-}" == "--version" ]]; then
    printf 'cmake version 3.30.0\\n'
    exit 0
fi
if [[ "${1:-}" == "--build" ]]; then
    build_dir="$2"
    mkdir -p "${build_dir}/assets/web"
    cp /bin/true "${build_dir}/sunshine"
    touch "${build_dir}/assets/web/index.html"
fi
exit 0
''',
            )
            for command in ("npm", "glslc"):
                (fake_bin / command).symlink_to("/bin/true")

            bash_env = fixture / "bash-env"
            bash_env.write_text('''command() {
    if [[ "${1:-}" == "-v" && "${2:-}" == "ccache" ]]; then
        return 1
    fi
    builtin command "$@"
}
''')
            env = os.environ.copy()
            env.update({
                "BASH_ENV": str(bash_env),
                "HOME": str(fixture / "home"),
                "PATH": f"{fake_bin}:/usr/bin:/bin",
                "XDG_CONFIG_HOME": str(fixture / "config"),
                "XDG_DATA_HOME": str(fixture / "data"),
                "MONITORIZE_TEST_CMAKE_LOG": str(fixture / "cmake.log"),
            })
            result = subprocess.run(
                [script, "--complete", "--cuda=off"],
                cwd=script.parent,
                env=env,
                stdin=subprocess.DEVNULL,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Bundled Sunshine installed", result.stdout)
            self.assertIn("Complete Install", result.stdout)
            cmake_log = (fixture / "cmake.log").read_text()
            self.assertIn("-DSUNSHINE_ENABLE_CUDA=OFF", cmake_log)
            self.assertIn("-DCUDA_FAIL_ON_MISSING=OFF", cmake_log)
            self.assertTrue((linux / "venv/bin/sunshine").is_file())
            self.assertTrue((fixture / "config/monitorize/sunshine-1/sunshine.conf").is_file())
            desktop_entry = fixture / "data/applications/monitorize.desktop"
            self.assertTrue(desktop_entry.is_file())
            if validator := shutil.which("desktop-file-validate"):
                validation = subprocess.run(
                    [validator, desktop_entry],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(validation.returncode, 0, validation.stderr)

            cuda_on_result = subprocess.run(
                [script, "--complete", "--cuda=on", "--rebuild-sunshine"],
                cwd=script.parent,
                env=env,
                stdin=subprocess.DEVNULL,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(cuda_on_result.returncode, 0, cuda_on_result.stderr)
            self.assertIn("CUDA enabled", cuda_on_result.stdout)
            cmake_log = (fixture / "cmake.log").read_text()
            self.assertIn("-DSUNSHINE_ENABLE_CUDA=ON", cmake_log)
            self.assertIn("-DCUDA_FAIL_ON_MISSING=ON", cmake_log)
            build_stamp = sunshine / "build/.monitorize-built-fingerprint"
            self.assertIn("cuda-policy=on|cuda=on", build_stamp.read_text())

            bash_env.write_text('''command() {
    if [[ "${1:-}" == "-v" && "${2:-}" =~ ^(ccache|git|cmake|node|npm|patch|cksum)$ ]]; then
        return 1
    fi
    builtin command "$@"
}
''')
            partial_result = subprocess.run(
                [script, "--partial"],
                cwd=script.parent,
                env=env,
                stdin=subprocess.DEVNULL,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(partial_result.returncode, 0, partial_result.stderr)
            self.assertIn("Partial Install", partial_result.stdout)

    def test_installer_builds_only_project_local_sunshine_backend(self):
        script = (ROOT / "linux/scripts/install.sh").read_text()
        requirements = (ROOT / "linux/requirements.txt").read_text()
        self.assertIn("external/sunshine", script)
        self.assertIn("MONITORIZE_BUILD_JOBS", script)
        self.assertIn("--depth 1 --jobs", script)
        self.assertIn("update_sunshine_submodules", script)
        self.assertIn("submodule sync --recursive", script)
        self.assertIn("getconf _NPROCESSORS_ONLN", script)
        self.assertIn("(( jobs > 8 )) && jobs=8", script)
        self.assertIn("rev-parse --verify HEAD", script)
        self.assertIn("--rebuild-sunshine", script)
        self.assertIn("--cuda=POLICY", script)
        self.assertIn("SUNSHINE_ENABLE_CUDA=ON", script)
        self.assertIn("SUNSHINE_ENABLE_CUDA=OFF", script)
        self.assertIn("cuda-policy=${CUDA_POLICY}", script)
        self.assertIn("CMAKE_FRESH_FLAGS=(--fresh)", script)
        self.assertIn(".monitorize-built-fingerprint", script)
        self.assertIn("CMAKE_C_COMPILER_LAUNCHER=ccache", script)
        self.assertIn("-G Ninja", script)
        self.assertIn("check_sunshine_node_modules_permissions", script)
        self.assertIn("Sunshine's generated npm cache is not writable", script)
        self.assertIn("sunshine-portal-token-scope.patch", script)
        self.assertIn("SUNSHINE_PORTAL_TOKEN_SCOPE", script)
        self.assertIn("Then rerun this installer without sudo.", script)
        self.assertIn("multi-GPU VA-API selection will be unavailable", script)
        self.assertIn("require_command patch", script)
        self.assertIn("require_command cksum", script)
        self.assertIn("--complete", script)
        self.assertIn("--partial", script)
        self.assertIn("Jinja2", requirements)
        self.assertNotIn("monitorize-rtp-sender", script)
        self.assertNotIn("cmake --install", script)
        self.assertNotIn("/usr/local", script)
        self.assertNotIn("cryptography", script)
        self.assertNotIn("zeroconf", script)
        self.assertNotIn("evdev", script)

        workflow = (ROOT / ".github/workflows/desktop.yml").read_text()
        self.assertIn("linux/scripts/install.sh --partial", workflow)

    def test_nix_closure_has_no_monitorize_gstreamer_or_adb_runtime(self):
        package = (ROOT / "nix/package.nix").read_text()
        self.assertIn("monitorizeSunshine", package)
        self.assertIn("sunshine-portal-token-scope.patch", package)
        self.assertNotIn("gst_all_1", package)
        self.assertNotIn("android-tools", package)
        self.assertNotIn("monitorize-rtp-sender", package)

    def test_qml_exposes_only_sunshine_display_flow(self):
        qml = "\n".join(
            path.read_text()
            for path in (ROOT / "linux/monitorize/qml").glob("*.qml")
        )
        self.assertIn("Create a Virtual Display", qml)
        for legacy in ("USB Mode", "Receiver Mode", 'model: ["Monitorize", "Sunshine"]'):
            self.assertNotIn(legacy, qml)

    def test_display_setup_groups_streaming_and_virtual_only_controls(self):
        qml = (ROOT / "linux/monitorize/qml/DisplaySetupPage.qml").read_text()
        headings = ('title: "DISPLAY"', 'title: "STREAMING"', 'title: "EXTRAS"')
        for heading in headings:
            self.assertIn(heading, qml)
        self.assertLess(qml.index(headings[0]), qml.index(headings[1]))
        self.assertLess(qml.index(headings[1]), qml.index(headings[2]))
        self.assertIn('model: ["Automatic (Recommended)", "Customize ›"]', qml)
        self.assertIn('text: "Create virtual display only"', qml)
        self.assertNotIn('text: "Launch"', qml)
        self.assertNotIn("Moonlight will discover", qml)
        self.assertIn(
            "Creates the virtual display without starting Monitorize’s streaming backend.",
            qml,
        )
        self.assertIn(
            'backend.setStreamingBackend(checked ? "none" : "sunshine")',
            qml,
        )

    def test_choice_chips_and_preset_menu_use_the_requested_layout(self):
        chips = (ROOT / "linux/monitorize/qml/ChoiceChips.qml").read_text()
        menu = (ROOT / "linux/monitorize/qml/MainMenuPage.qml").read_text()
        self.assertIn("columns: 3", chips)
        self.assertIn('text: "⋮"', menu)
        self.assertIn('text: "Rename"', menu)
        self.assertIn('text: "Remove"', menu)
        self.assertIn("backend.renamePreset", menu)
        self.assertNotIn('text: "×"', menu)

    def test_successful_pairing_closes_the_pin_popup(self):
        qml = (ROOT / "linux/monitorize/qml/StreamingPage.qml").read_text()
        self.assertIn("interval: 2000", qml)
        self.assertIn('if (result["success"]) pinSuccessCloseTimer.restart()', qml)
        self.assertIn("onTriggered: pinPopup.close()", qml)

    def test_navigation_direction_and_display_picker_actions_are_unambiguous(self):
        main = (ROOT / "linux/monitorize/qml/main.qml").read_text()
        combo = (ROOT / "linux/monitorize/qml/CustomComboBox.qml").read_text()
        display_setup = (ROOT / "linux/monitorize/qml/DisplaySetupPage.qml").read_text()
        streaming = (ROOT / "linux/monitorize/qml/StreamingPage.qml").read_text()

        self.assertIn("function pageOrder(page)", main)
        self.assertIn("property int pageTransitionDirection: 1", main)
        self.assertIn(
            "pageTransitionDirection = pageOrder(page) > pageOrder(selectedPage) ? 1 : -1",
            main,
        )
        self.assertIn(
            "from: root.pageTransitionDirection * stack.height",
            main,
        )
        self.assertIn(
            "to: -root.pageTransitionDirection * stack.height",
            main,
        )
        self.assertIn("property int disabledIndex: -1", combo)
        self.assertIn("enabled: index !== cb.disabledIndex", combo)
        self.assertIn("indicator: Text", combo)
        self.assertIn("color: theme.textPrimary", combo)
        self.assertIn("disabledIndex: 0", display_setup)
        self.assertEqual(streaming.count('text: "Pair Moonlight PIN"'), 1)

    def test_choice_chips_and_start_card_fit_their_containers(self):
        chips = (ROOT / "linux/monitorize/qml/ChoiceChips.qml").read_text()
        menu = (ROOT / "linux/monitorize/qml/MainMenuPage.qml").read_text()
        self.assertIn("GridLayout", chips)
        self.assertIn("columns: 3", chips)
        self.assertIn("rowSpacing: 8", chips)
        self.assertIn("Layout.preferredWidth: Math.min(440, page.width - 40)", menu)

    def test_retired_runtime_modules_are_absent(self):
        package = ROOT / "linux/monitorize"
        retired = (
            "desktop/receiver_controller.py",
            "desktop/usb_controller.py",
            "desktop/discovery_service.py",
            "input_bridge",
            "streaming/gst_session.py",
            "streaming/pipeline_builder.py",
            "streaming/audio_sender.py",
        )
        for relative_path in retired:
            self.assertFalse((package / relative_path).exists(), relative_path)

    def test_fedora_rpm_bundles_the_private_sunshine_backend(self):
        spec = (ROOT / "packaging/rpm/monitorize.spec").read_text()
        builder = (ROOT / "packaging/rpm/build.sh").read_text()
        pyproject = (ROOT / "pyproject.toml").read_text()

        project_version = re.search(r'^version = "([^"]+)"', pyproject, re.MULTILINE).group(1)
        spec_version = re.search(r'^Version:\s+(\S+)', spec, re.MULTILINE).group(1)
        self.assertEqual(project_version, spec_version)

        self.assertIn("fedora:${FEDORA_VERSION}", builder)
        self.assertIn("git submodule status --recursive", builder)
        self.assertIn("rpmbuild -ba", builder)
        self.assertIn("Smoke-testing", builder)
        self.assertLess(
            builder.index("test ! -e /root/.config/monitorize"),
            builder.index("from monitorize.desktop import main_window"),
        )
        self.assertIn("%{_libexecdir}/monitorize/sunshine", spec)
        self.assertIn("%{_libexecdir}/monitorize/monitorize-system-setup", spec)
        self.assertIn("io.github.vinnavannewton.monitorize.system-setup.policy", spec)
        self.assertIn("Requires:       polkit", spec)
        self.assertIn("%dir %{_datadir}/monitorize", spec)
        self.assertIn("%dir %{_datadir}/monitorize/sunshine", spec)
        self.assertIn("MONITORIZE_SUNSHINE_BIN", spec)
        self.assertIn("MONITORIZE_SUNSHINE_ASSETS_DIR", spec)
        self.assertIn("sunshine-portal-token-scope.patch", spec)
        self.assertIn("sunshine_ffmpeg_sha256", spec)
        self.assertIn("BuildRequires:  boost-devel >= 1.89.0", spec)
        self.assertIn("BuildRequires:  firewalld-filesystem", spec)
        self.assertIn("Requires(post): kmod", spec)
        self.assertIn("printf 'uinput\\n'", spec)
        self.assertIn("%{_modulesloaddir}/monitorize.conf", spec)
        self.assertIn("/usr/sbin/modprobe uinput", spec)
        self.assertNotIn("Source3:", spec)
        self.assertIn("%sysusers_create_compat", spec)
        self.assertNotIn("cmake --install", spec)
        self.assertNotIn("-m py_compile %{buildroot}%{_libexecdir}/monitorize/monitorize-system-setup", spec)
        self.assertNotRegex(spec, r"install .*%\{_bindir\}/sunshine")
        self.assertNotRegex(spec, r"install .*?/usr/local")
        for retired_dependency in ("gstreamer", "android-tools", "zeroconf", "python3-evdev"):
            self.assertNotIn(retired_dependency, spec.lower())

    def test_fedora_uinput_access_uses_the_dedicated_group(self):
        rules = (ROOT / "packaging/fedora/70-monitorize-uinput.rules").read_text()
        sysusers = (ROOT / "packaging/fedora/monitorize.sysusers").read_text()
        self.assertIn('GROUP="monitorize-input"', rules)
        self.assertEqual(sysusers.strip(), "g monitorize-input - -")
        self.assertNotIn("Monitorize-Touch", rules)


if __name__ == "__main__":
    unittest.main()
