

import argparse
import json
import sys
import threading

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst


def build_pipeline(host, port, rotation, sink="autovideosink"):
    return Gst.parse_launch(
        f"tcpclientsrc host={json.dumps(host)} port={port} ! h264parse ! avdec_h264 ! "
        "queue max-size-buffers=2 leaky=downstream ! videoconvert ! "
        f"videoflip name=rotate video-direction={rotation} ! {sink} sync=false"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("port", type=int)
    parser.add_argument("rotation", type=int, choices=range(4))
    args = parser.parse_args()

    Gst.init(None)
    pipeline = build_pipeline(args.host, args.port, args.rotation)
    rotate = pipeline.get_by_name("rotate")
    loop = GLib.MainLoop()
    exit_code = 0

    def handle_message(_bus, message):
        nonlocal exit_code
        if message.type == Gst.MessageType.ERROR:
            error, _debug = message.parse_error()
            print(f"ERROR: {error}", flush=True)
            exit_code = 1
            loop.quit()
        elif message.type == Gst.MessageType.EOS:
            print("EOS received", flush=True)
            loop.quit()

    def read_commands():
        for line in sys.stdin:
            command, _, value = line.strip().partition(" ")
            if command == "ROTATE" and value in {"0", "1", "2", "3"}:
                GLib.idle_add(rotate.set_property, "video-direction", int(value))

    bus = pipeline.get_bus()
    bus.add_signal_watch()
    bus.connect("message", handle_message)
    threading.Thread(target=read_commands, daemon=True).start()

    if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
        print("ERROR: Failed to start receiver pipeline", flush=True)
        return 1
    print("Stream connected and playing", flush=True)
    try:
        loop.run()
    finally:
        pipeline.set_state(Gst.State.NULL)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
