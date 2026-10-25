"""Full-screen captures through KWin's ScreenShot2 D-Bus interface, as numpy RGB arrays.

KWin writes the raw pixels into a pipe, which takes about 15 ms for a 3440x1440 frame -
fast enough to pick, among consecutive frames, the one that matches another run's.
KWin only answers an executable a desktop file authorises; wkbench.py writes that file
into the nested session's private data folder.
"""
import os

import dbus
import numpy as np

# QImage::Format_RGBX8888, RGBA8888 and RGBA8888_Premultiplied: bytes R, G, B, then X or A
BYTE_ORDER_RGBA = (16, 17, 18)


class Grabber:
    def __init__(self):
        bus = dbus.SessionBus()
        self.iface = dbus.Interface(bus.get_object("org.kde.KWin", "/org/kde/KWin/ScreenShot2"),
                                    "org.kde.KWin.ScreenShot2")

    def grab(self):
        read, write = os.pipe()
        try:
            info = self.iface.CaptureWorkspace({"native-resolution": True}, dbus.types.UnixFd(write))
        finally:
            os.close(write)
        width, height, stride = int(info["width"]), int(info["height"]), int(info["stride"])
        if int(info["format"]) not in BYTE_ORDER_RGBA:
            raise RuntimeError(f"Unexpected screenshot format {int(info['format'])}")
        data = bytearray()
        with os.fdopen(read, "rb") as source:
            while len(data) < stride * height:
                chunk = source.read(stride * height - len(data))
                if not chunk:
                    break
                data += chunk
        pixels = np.frombuffer(bytes(data), dtype=np.uint8).reshape(height, stride // 4, 4)
        return pixels[:, :width, :3]
