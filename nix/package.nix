{ lib
, python3Packages
, qt6
, gobject-introspection
, kdePackages
, copyDesktopItems
, makeDesktopItem
, bash
, pkg-config
, wayland
, stdenv
, sunshine
, buildNpmPackage
, importNpmLock
, fetchurl
, runCommand
, gnutar
, gzip
, libva-utils
, cudaSupport ? (stdenv.hostPlatform.system == "x86_64-linux")
}:

let
  python = python3Packages.python;
  sunshineSource = lib.cleanSource ../external/sunshine;
  sunshineVersion = "0-unstable-2026-10-03";
  sunshineForBuild = sunshine.override { inherit cudaSupport; };
  ffmpegArch = {
    x86_64-linux = "Linux-x86_64";
    aarch64-linux = "Linux-aarch64";
  }.${stdenv.hostPlatform.system};
  ffmpegArchive = fetchurl {
    url = "https://github.com/LizardByte/build-deps/releases/download/v2026.910.121303/${ffmpegArch}-ffmpeg.tar.gz";
    hash = {
      x86_64-linux = "sha256-SW0ru2dNAeYDPjG538FcvJ3BSU6IKkUF9qseA/dbOFw=";
      aarch64-linux = "sha256-IfmUCeGroJGR+d8oaI+3+dRPmOZTeKZRMkxjLBmCOqA=";
    }.${stdenv.hostPlatform.system};
  };
  ffmpegPrepared = runCommand "monitorize-sunshine-ffmpeg" {
    nativeBuildInputs = [ gnutar gzip ];
  } ''
    mkdir -p "$out"
    tar -xzf ${ffmpegArchive} -C "$out" --strip-components=1
  '';
  sunshineUi = buildNpmPackage {
    pname = "monitorize-sunshine-ui";
    version = sunshineVersion;
    src = sunshineSource;
    npmDeps = importNpmLock { npmRoot = sunshineSource; };
    npmConfigHook = importNpmLock.npmConfigHook;
    installPhase = ''
      runHook preInstall
      mkdir -p "$out"
      cp -r build "$out/build"
      runHook postInstall
    '';
  };
  monitorizeSunshine = sunshineForBuild.overrideAttrs (finalAttrs: previousAttrs: {
    pname = "monitorize-sunshine";
    version = sunshineVersion;
    src = sunshineSource;
    ui = sunshineUi;
    patches = (previousAttrs.patches or []) ++ [
      ../packaging/sunshine-strict-selection.patch
      # Portal token scoping is already part of the pinned Monitorize fork.
    ];
    cmakeFlags = builtins.filter
      (flag: !(lib.hasPrefix "-DFFMPEG_PREPARED_BINARIES=" flag))
      previousAttrs.cmakeFlags ++ [
        (lib.cmakeFeature "FFMPEG_PREPARED_BINARIES" "${ffmpegPrepared}")
        (lib.cmakeBool "SUNSHINE_ENABLE_CUDA" cudaSupport)
        (lib.cmakeBool "CUDA_FAIL_ON_MISSING" cudaSupport)
        (lib.cmakeBool "SUNSHINE_ENABLE_VAAPI" true)
        (lib.cmakeBool "SUNSHINE_ENABLE_KWIN" true)
        (lib.cmakeBool "SUNSHINE_ENABLE_WAYLAND" true)
        (lib.cmakeBool "SUNSHINE_ENABLE_PORTAL" true)
        (lib.cmakeBool "SUNSHINE_ENABLE_TRAY" false)
        (lib.cmakeBool "BUILD_TESTS" false)
        (lib.cmakeBool "BUILD_DOCS" false)
      ];
    env = previousAttrs.env // {
      BUILD_VERSION = finalAttrs.version;
      BRANCH = "monitorize";
      COMMIT = "8d043f2b929705a4f6bad30d1e7f700a2de607b6";
    };
  });
in
python3Packages.buildPythonApplication rec {
  pname = "monitorize";
  version = "0.33.3";
  pyproject = false;                    # Nix installs the linux/ tree directly.

  # Use lib.cleanSource to exclude editor artefacts, __pycache__, venv/, etc.
  # so only the intended tree is packaged and builds remain reproducible.
  src = lib.cleanSource ../linux;

  nativeBuildInputs = [
    qt6.wrapQtAppsHook
    copyDesktopItems
    gobject-introspection
    pkg-config
    wayland
    bash
  ];

  buildInputs = [
    qt6.qtbase
    qt6.qtdeclarative                   # QML engine
    qt6.qtsvg
    qt6.qtwayland
    wayland
  ];

  # Single, authoritative dependency list.
  # PYTHONPATH in postFixup is derived from this via python3Packages.makePythonPath
  # so the two can never get out of sync.
  propagatedBuildInputs = [
    python3Packages.pyqt6
    python3Packages.pyqt6-sip
    python3Packages.dbus-python
    python3Packages.pygobject3
    gobject-introspection
  ];

  # ── Install phase ──────────────────────────────────────────────────
  installPhase = ''
    runHook preInstall

    # Python package
    siteDir="$out/${python.sitePackages}"
    mkdir -p "$siteDir"
    cp -r monitorize "$siteDir/"

    # Launcher script – use an explicit store bash so the wrapper is
    # fully hermetic and does not depend on /usr/bin/env or a host bash.
    mkdir -p "$out/bin"
    cat > "$out/bin/monitorize" <<WRAPPER
    #!${bash}/bin/bash
    exec ${python}/bin/python3 -m monitorize "\$@"
    WRAPPER
    chmod +x "$out/bin/monitorize"

    # Patch /usr/bin/env shebangs before executing build scripts.
    patchShebangs native

    # Native KWin virtual-output owner. The hidden desktop entry below grants
    # this exact executable access to KWin's restricted screencast protocol.
    native/kde_virtual_output/build.sh \
      "$out/bin/monitorize-kde-virtual-output"
    mkdir -p "$out/share/applications"
    cat > "$out/share/applications/monitorize-kde-virtual-output.desktop" <<EOF
    [Desktop Entry]
    Type=Application
    Name=Monitorize KDE Virtual Output
    Exec=$out/bin/monitorize-kde-virtual-output
    NoDisplay=true
    Terminal=false
    X-KDE-Wayland-Interfaces=zkde_screencast_unstable_v1
    EOF

    # Icon
    mkdir -p "$out/share/icons/hicolor/192x192/apps"
    cp monitorize/assets/monitorize_desktop_logo.png \
       "$out/share/icons/hicolor/192x192/apps/monitorize.png"

    runHook postInstall
  '';

  desktopItems = [
    (makeDesktopItem {
      name = "monitorize";
      desktopName = "Monitorize";
      comment = "Create Sunshine virtual displays for Moonlight clients";
      exec = "monitorize";
      icon = "monitorize";
      terminal = false;
      categories = [ "Utility" "System" ];
      keywords = [ "monitor" "display" "moonlight" "sunshine" "screen" "extend" "mirror" "streaming" ];
      startupNotify = true;
      startupWMClass = "monitorize";
    })
  ];

  # ── Wrap the launcher with all required paths ──────────────────────
  dontWrapQtApps = true;  # we do it ourselves so we can merge everything

  postFixup = ''
    # Derive PYTHONPATH from propagatedBuildInputs so it never drifts out of
    # sync with the dependency list above.
    pythonPath="${python3Packages.makePythonPath propagatedBuildInputs}:$out/${python.sitePackages}"

    wrapProgram "$out/bin/monitorize" \
      "''${qtWrapperArgs[@]}" \
      --prefix PYTHONPATH : "$pythonPath" \
      --prefix PATH : "${lib.makeBinPath [
        kdePackages.libkscreen
        libva-utils
        monitorizeSunshine
      ]}" \
      --set MONITORIZE_SUNSHINE_BIN "${monitorizeSunshine}/bin/sunshine" \
      --set MONITORIZE_SUNSHINE_ASSETS_DIR "${monitorizeSunshine}/assets"
  '';

  # Skip automatic tests (they need a running display server)
  doCheck = false;

  meta = with lib; {
    description = "Create Sunshine-backed virtual displays for Moonlight clients";
    homepage = "https://github.com/vinnavannewton/project-monitorize";
    license = licenses.gpl3Only;
    platforms = platforms.linux;
    maintainers = [ ];
    mainProgram = "monitorize";
  };
}
