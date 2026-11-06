"""Looper OSS -- a touchscreen loop station that runs as an application.

The package has two jobs:

* `main`   -- own the whole stack (static UI server, OSC bridge, SuperCollider
              engine) and supervise it. No shell script anywhere in the path.
* `window` -- put the UI in a real window: GTK+WebKit2 natively, an Edge/Chromium
              `--app` window on Windows, or the default browser as a last resort.
"""
