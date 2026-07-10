effects.desktopChanged.connect(function(old, current, w, output) {
    sendTestResponse("desktopChanged - " + old.x11DesktopNumber + " " + current.x11DesktopNumber + " " + output.name);
});
