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
}:

let
  python = python3Packages.python;
  pythonPath = python3Packages.makePythonPath [
    python3Packages.pyqt6
    python3Packages.pyqt6-sip
    python3Packages.dbus-python
    python3Packages.pygobject3
    python3Packages.zeroconf
    python3Packages.evdev
    python3Packages.cryptography
  ];
in
python3Packages.buildPythonApplication rec {
  pname = "monitorize";
  version = "0-unstable";
  pyproject = false;                    # no setup.py / pyproject.toml yet

  src = ../linux;

  nativeBuildInputs = [
    qt6.wrapQtAppsHook
    copyDesktopItems
    gobject-introspection
  ];

  buildInputs = [
    qt6.qtbase
    qt6.qtdeclarative                   # QML engine
    qt6.qtwayland
  ];

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

    # Launcher script
    mkdir -p "$out/bin"
    cat > "$out/bin/monitorize" <<'WRAPPER'
    #!/usr/bin/env bash
    exec @python@ -m monitorize "$@"
    WRAPPER
    substituteInPlace "$out/bin/monitorize" --replace-fail "@python@" "${python}/bin/python3"
    chmod +x "$out/bin/monitorize"

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
    wrapProgram "$out/bin/monitorize" \
      "''${qtWrapperArgs[@]}" \
      --prefix PYTHONPATH : "${pythonPath}:$out/${python.sitePackages}" \
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
