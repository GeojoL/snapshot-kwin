"""KWin ScreenShot2 取图。

KWin 只允许 .desktop 里声明了 X-KDE-DBUS-Restricted-Interfaces=org.kde.KWin.ScreenShot2
的可执行文件调用这个接口(按调用进程的 /proc/<pid>/exe 匹配),所以守护进程必须由
packaging/ 里那份专用解释器副本运行。实测 2560x1440 整屏 55-75 ms。
"""
import os

import cairo
import dbus

# KWin 返回的是 QImage::Format 编号。小端机器上下面两种都是 BGRA 字节序,
# 与 cairo.FORMAT_ARGB32(预乘)/ RGB24 的内存布局一致,可以零转换直接用。
_QIMAGE_RGB32 = 4
_QIMAGE_ARGB32 = 5
_QIMAGE_ARGB32_PREMULTIPLIED = 6


class CaptureError(RuntimeError):
    pass


class KWinCapture:
    def __init__(self, bus=None):
        self._bus = bus or dbus.SessionBus()
        obj = self._bus.get_object("org.kde.KWin.ScreenShot2", "/org/kde/KWin/ScreenShot2")
        self._iface = dbus.Interface(obj, "org.kde.KWin.ScreenShot2")

    def workspace(self):
        """整个桌面(所有屏幕)。返回 (cairo.ImageSurface, scale)。"""
        return self._call("CaptureWorkspace", {"native-resolution": True})

    def _call(self, method, options, *args):
        r, w = os.pipe()
        try:
            meta = getattr(self._iface, method)(*args, options, dbus.types.UnixFd(w))
        except dbus.DBusException as e:
            os.close(r)
            raise CaptureError(f"{e.get_dbus_name()}: {e.get_dbus_message()}") from e
        finally:
            os.close(w)
        chunks = []
        while True:
            b = os.read(r, 1 << 22)
            if not b:
                break
            chunks.append(b)
        os.close(r)
        data = bytearray(b"".join(chunks))
        width, height = int(meta["width"]), int(meta["height"])
        stride = int(meta["stride"])
        fmt = int(meta["format"])
        if fmt == _QIMAGE_RGB32:
            cfmt = cairo.FORMAT_RGB24
        elif fmt in (_QIMAGE_ARGB32, _QIMAGE_ARGB32_PREMULTIPLIED):
            cfmt = cairo.FORMAT_ARGB32
        else:
            raise CaptureError(f"unsupported QImage format {fmt}")
        if len(data) < stride * height:
            raise CaptureError(f"short read: {len(data)} < {stride * height}")
        surface = cairo.ImageSurface.create_for_data(data, cfmt, width, height, stride)
        scale = float(meta.get("scale", 1.0))
        return surface, scale
