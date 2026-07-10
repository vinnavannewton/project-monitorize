'''org.freedesktop.Screensaver proxy mock template
'''







__author__ = 'Jonas Ådahl'
__copyright__ = '(c) 2023 Red Hat Inc.'

import dbus
import os
import random
from dbusmock import MOCK_IFACE


BUS_NAME = 'org.freedesktop.Screensaver'
MAIN_OBJ = '/org/freedesktop/Screensaver'
MAIN_IFACE = BUS_NAME
SYSTEM_BUS = False


def load(mock, parameters=None):
    pass


@dbus.service.method(MAIN_IFACE, in_signature='ss', out_signature='u')
def Inhibit(self, application_name, reason):
    return random.randint(0, 10000)

@dbus.service.method(MAIN_IFACE, in_signature='u')
def Uninhibit(self, cookie):
    pass
