{ lib
, python3Packages
, qt6
, gst_all_1
, pipewire
, gobject-introspection
, android-tools
, kdePackages
, wlr-randr
, xdg-desktop-portal
, xdg-desktop-portal-hyprland
, xdg-desktop-portal-gtk
, copyDesktopItems
, makeDesktopItem
, bash
, pkg-config
, wayland
}:

let
  python = python3Packages.python;
in
python3Packages.buildPythonApplication rec {
  pname = "monitorize";
  version = "0-unstable";
  pyproject = false;                    # no setup.py / pyproject.toml yet

  # Use lib.cleanSource to exclude editor artefacts, __pycache__, venv/, etc.
  # so only the intended tree is packaged and builds remain reproducible.
  src = lib.cleanSource ../linux;

  nativeBuildInputs = [
    qt6.wrapQtAppsHook
    copyDesktopItems
    gobject-introspection
    pkg-config
    wayland
  ];

  buildInputs = [
    qt6.qtbase
    qt6.qtdeclarative                   # QML engine
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
    python3Packages.zeroconf
    python3Packages.evdev
    python3Packages.cryptography
    # GStreamer runtime
    gst_all_1.gstreamer
    gst_all_1.gst-plugins-base
    gst_all_1.gst-plugins-good
    gst_all_1.gst-plugins-bad
    gst_all_1.gst-plugins-ugly
    # Introspection typelibs for Gst
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
      comment = "Linux to Android Display Bridge – extend or mirror your desktop to a tablet";
      exec = "monitorize";
      icon = "monitorize";
      terminal = false;
      categories = [ "Utility" "System" ];
      keywords = [ "monitor" "display" "tablet" "android" "screen" "extend" "mirror" "streaming" ];
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
        gst_all_1.gstreamer
        android-tools
        kdePackages.libkscreen
        wlr-randr
        xdg-desktop-portal
        xdg-desktop-portal-hyprland
        xdg-desktop-portal-gtk
        pipewire
      ]}" \
      --prefix GST_PLUGIN_SYSTEM_PATH_1_0 : "${lib.makeSearchPathOutput "lib" "lib/gstreamer-1.0" [
        gst_all_1.gstreamer
        gst_all_1.gst-plugins-base
        gst_all_1.gst-plugins-good
        gst_all_1.gst-plugins-bad
        gst_all_1.gst-plugins-ugly
        pipewire
      ]}" \
      --prefix GI_TYPELIB_PATH : "${lib.makeSearchPathOutput "out" "lib/girepository-1.0" [
        gst_all_1.gstreamer
        gst_all_1.gst-plugins-base
        gobject-introspection
      ]}"
  '';

  # Skip automatic tests (they need a running display server)
  doCheck = false;

  meta = with lib; {
    description = "Turn your Android / Linux laptop into a secondary monitor for your Linux desktop";
    homepage = "https://github.com/vinnavannewton/ProjectMonitorize";
    license = licenses.agpl3Plus;
    platforms = platforms.linux;
    maintainers = [ ];
    mainProgram = "monitorize";
  };
}
