import sys, os, time, select
import snegg.ei as ei
import snegg.oeffis as oeffis

ctx = oeffis.Oeffis.create(devices=oeffis.DeviceType.TOUCHSCREEN)
deadline = time.monotonic() + 10.0
eis_fd = None
while time.monotonic() < deadline:
    ready, _, _ = select.select([ctx.fd.fileno()], [], [], 1.0)
    if ready and ctx.dispatch():
        eis_fd = ctx.eis_fd
        break

if not eis_fd:
    print("No portal")
    sys.exit(0)

io_fd = os.fdopen(eis_fd, "rb", buffering=0)
sender = ei.Sender.create_for_fd(io_fd, name="test")

deadline = time.monotonic() + 5.0
while time.monotonic() < deadline:
    ready, _, _ = select.select([sender.fd], [], [], 0.5)
    if ready:
        sender.dispatch()
    for ev in sender.events:
        t = ei.libei.event_get_type(ev._cobject)
        if t == 3: # SEAT_ADDED
            ev.seat.bind((ei.DeviceCapability.TOUCH,))
        elif t == 5: # DEVICE_ADDED
            dev = ev.device
            print(f"Device: {dev.name}, width={dev.width}, height={dev.height}")
            if hasattr(dev, 'regions') and dev.regions:
                for r in dev.regions:
                    print(f"Region: {r.position} dim={r.dimension} scale={r.physical_scale}")
            else:
                print("No regions")
            sys.exit(0)
