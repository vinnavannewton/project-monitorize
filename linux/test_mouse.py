import time
from evdev import UInput, ecodes, AbsInfo

events = {
    ecodes.EV_KEY: [ecodes.BTN_LEFT, ecodes.BTN_RIGHT],
    ecodes.EV_ABS: [
        (ecodes.ABS_X, AbsInfo(0, 0, 65535, 0, 0, 0)),
        (ecodes.ABS_Y, AbsInfo(0, 0, 65535, 0, 0, 0)),
    ],
}
dev = UInput(events, name="Test Absolute Mouse")
time.sleep(1) # wait for udev
print("Moving mouse to 32767, 32767")
dev.write(ecodes.EV_ABS, ecodes.ABS_X, 32767)
dev.write(ecodes.EV_ABS, ecodes.ABS_Y, 32767)
dev.syn()
time.sleep(1)
dev.close()
