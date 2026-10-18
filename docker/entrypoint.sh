#!/bin/sh
# Virtual ekran, VNC (faqat konteyner ichida), noVNC web (8107), dashboard (8106).
set -e
rm -f /tmp/.X99-lock
Xvfb :99 -screen 0 1366x900x24 -nolisten tcp &
sleep 1
x11vnc -display :99 -localhost -nopw -forever -shared -quiet -rfbport 5900 &
websockify --web /usr/share/novnc 0.0.0.0:8107 127.0.0.1:5900 >/dev/null 2>&1 &
exec python dashboard.py
