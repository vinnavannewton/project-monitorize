'''gsd-color proxy mock template
'''







__author__ = 'Jonas Ådahl'
__copyright__ = '(c) 2021 Red Hat Inc.'

import dbus
import os
from dbusmock import MOCK_IFACE


BUS_NAME = 'org.gnome.SettingsDaemon.Color'
MAIN_OBJ = '/org/gnome/SettingsDaemon/Color'
MAIN_IFACE = BUS_NAME
SYSTEM_BUS = False


def load(mock, parameters=None):
    mock.night_light_active = False
    mock.temperature = 6500

    mock.AddProperty(MAIN_IFACE, 'NightLightActive', dbus.Boolean())
    mock.AddProperty(MAIN_IFACE, 'Temperature', dbus.UInt32())
    mock.Set(MAIN_IFACE, 'NightLightActive', mock.night_light_active)
    mock.Set(MAIN_IFACE, 'Temperature', dbus.UInt32(mock.temperature))


@dbus.service.method(MOCK_IFACE, in_signature='b')
def SetNightLightActive(self, active):
    self.UpdateProperties(MAIN_IFACE, {'NightLightActive': active})

@dbus.service.method(MOCK_IFACE, in_signature='u')
def SetTemperature(self, temperature):
    self.UpdateProperties(MAIN_IFACE, {'Temperature': temperature})
