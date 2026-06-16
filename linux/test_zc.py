from zeroconf import Zeroconf
import time

zc = Zeroconf()
time.sleep(1)
zc.close()
print("Zeroconf closed")
