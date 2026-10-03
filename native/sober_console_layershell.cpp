// A small shim: LayerShellQt has a C++ API only (no Python bindings exist), so this exposes
// the two calls sober-console needs as plain C functions that Python loads with ctypes
// (sober_console/layershell.py).  Same approach as afterglow's native/ shim.
//
// Build against the SAME Qt as PySide6 -- see flake.nix.
#include <LayerShellQt/Window>
#include <QMargins>
#include <QWindow>

// keyboard: 0 = never takes keyboard focus, 1 = on demand (clicking the surface focuses it)
static LayerShellQt::Window::KeyboardInteractivity toInteractivity(int keyboard)
{
    return keyboard == 1 ? LayerShellQt::Window::KeyboardInteractivityOnDemand
                         : LayerShellQt::Window::KeyboardInteractivityNone;
}

extern "C" {

// Turns an existing, not-yet-shown QWindow into an overlay-layer surface:
//   layer            overlay (above fullscreen windows)
//   exclusive zone   -1 (ignore other surfaces' zones, so with all four anchors the surface covers
//                    the whole output; it never pushes other windows around)
//   anchors          LayerShellQt::Window::Anchor bits: top=1 bottom=2 left=4 right=8
//   margins          pixels from the anchored edges
// The output is the window's own QScreen (set it with QWindow::setScreen first).
// Returns 0 on success, -1 if the window isn't usable as a layer surface.
int sober_console_configure(void *qwindow, int anchors, int top, int right, int bottom, int left, int keyboard)
{
    auto *window = static_cast<QWindow *>(qwindow);
    if (!window) {
        return -1;
    }
    auto *ls = LayerShellQt::Window::get(window);
    if (!ls) {
        return -1;
    }
    ls->setScope(QStringLiteral("sober-console"));
    ls->setLayer(LayerShellQt::Window::LayerOverlay);
    ls->setKeyboardInteractivity(toInteractivity(keyboard));
    ls->setExclusiveZone(-1);
    ls->setAnchors(LayerShellQt::Window::Anchors(anchors));
    ls->setMargins(QMargins(left, top, right, bottom));
    return 0;
}

int sober_console_set_keyboard(void *qwindow, int keyboard)
{
    auto *window = static_cast<QWindow *>(qwindow);
    if (!window) {
        return -1;
    }
    auto *ls = LayerShellQt::Window::get(window);
    if (!ls) {
        return -1;
    }
    ls->setKeyboardInteractivity(toInteractivity(keyboard));
    return 0;
}

} // extern "C"
