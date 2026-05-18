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

touch_dev = None

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
            touch_dev = ev.device
            print(f"Device: {touch_dev.name}")
            touch_dev.start_emulating()
            break
    if touch_dev:
        break

if not touch_dev:
    print("No device")
    sys.exit(1)

# Now inject a touch down, motion, up
print("Injecting touch at 100, 100")
touch = touch_dev.touch_new()
touch.down(100, 100)
touch_dev.frame()

time.sleep(1)

print("Injecting touch motion to 200, 200")
touch.motion(200, 200)
touch_dev.frame()

time.sleep(1)

print("Injecting touch up")
touch.up()
touch_dev.frame()

print("Done")
