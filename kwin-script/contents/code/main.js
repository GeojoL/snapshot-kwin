// snapshot-kwin 窗口位置推送(常驻 KWin 脚本)。
// 每当窗口新增/关闭/移动/改大小/切换焦点,就把当前桌面上可见普通窗口的
// 几何与叠放顺序(从下到上)推给守护进程,点选窗口时无需再查询 KWin。
const SERVICE = "io.github.geojol.SnapshotKwin";
const PATH = "/io/github/geojol/SnapshotKwin";
const IFACE = "io.github.geojol.SnapshotKwin";

function snapshot() {
    const out = [];
    const order = workspace.stackingOrder;
    for (let i = 0; i < order.length; i++) {
        const w = order[i];
        if (!w || !w.normalWindow || w.minimized || w.hidden) continue;
        if (!(w.onAllDesktops || w.desktops.indexOf(workspace.currentDesktop) >= 0)) continue;
        if (w.resourceClass === "snapshot-kwin" || w.caption === "snapshot-kwin-overlay") continue;
        const g = w.frameGeometry;
        out.push({ x: g.x, y: g.y, w: g.width, h: g.height, cls: String(w.resourceClass), title: String(w.caption) });
    }
    callDBus(SERVICE, PATH, IFACE, "UpdateWindows", JSON.stringify(out));
}

function watch(w) {
    if (!w) return;
    w.frameGeometryChanged.connect(snapshot);
    w.minimizedChanged.connect(snapshot);
}

for (const w of workspace.windowList()) watch(w);
workspace.windowAdded.connect(function (w) { watch(w); snapshot(); });
workspace.windowRemoved.connect(snapshot);
workspace.windowActivated.connect(snapshot);
workspace.currentDesktopChanged.connect(snapshot);
snapshot();
