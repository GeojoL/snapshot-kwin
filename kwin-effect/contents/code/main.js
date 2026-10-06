"use strict";
// Window open/close effects (e.g. Scale) skip or cancel their animation when
// another effect grabs the window. We grab only snapshot-kwin's own windows and
// do nothing with them, so the frozen-screen overlay and the clipboard picker
// appear instantly. Every other window keeps its normal animation.
const APP_ID = "io.github.geojol.SnapshotKwin";

function isOurs(w) {
    return w && String(w.windowClass).indexOf(APP_ID) !== -1;
}

effects.windowAdded.connect(function (w) {
    if (isOurs(w)) effect.grab(w, Effect.WindowAddedGrabRole);
});
effects.windowClosed.connect(function (w) {
    if (isOurs(w)) effect.grab(w, Effect.WindowClosedGrabRole);
});
