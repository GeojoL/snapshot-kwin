import dbus, os, time, sys
bus = dbus.SessionBus()
obj = bus.get_object("org.kde.KWin.ScreenShot2", "/org/kde/KWin/ScreenShot2")
iface = dbus.Interface(obj, "org.kde.KWin.ScreenShot2")
for n in range(int(sys.argv[1]) if len(sys.argv)>1 else 3):
    r, w = os.pipe()
    t0 = time.perf_counter()
    try:
        meta = iface.CaptureWorkspace({"native-resolution": True}, dbus.types.UnixFd(w))
    except dbus.DBusException as e:
        print("拒绝:", e.get_dbus_name(), e.get_dbus_message()); sys.exit(1)
    os.close(w); t1 = time.perf_counter()
    size = 0
    while True:
        b = os.read(r, 1 << 20)
        if not b: break
        size += len(b)
    os.close(r); t2 = time.perf_counter()
    print(f"第{n+1}次: D-Bus 返回 {(t1-t0)*1000:.0f} ms, 读完像素 {(t2-t0)*1000:.0f} ms, {int(meta['width'])}x{int(meta['height'])}, {size/1e6:.1f} MB")
