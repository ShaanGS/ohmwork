#!/bin/sh
# Start the display, then BECOME the server.
#
# This replaces `xvfb-run` as the container's command, for two reasons and one
# measurement.
#
# THE MEASUREMENT. With `CMD ["xvfb-run", "-a", "python", "-m",
# "ohmwork.server"]` the container came up, stayed up, bound nothing, and
# wrote NOTHING to `docker logs` for eighty seconds. A process that is alive
# and silent is the hardest kind to diagnose, and it was silent because
# xvfb-run was between the server and the log.
#
# THE TWO REASONS to prefer this shape anyway:
#
#   `exec` -- the server becomes PID 1, so its stdout IS the container's log
#   and `docker stop` sends SIGTERM to the thing that should handle it.
#   Under xvfb-run the server was a grandchild that never saw the signal.
#
#   an explicit display -- Xvfb on a known number, started and left running,
#   rather than xvfb-run's temporary server and authority file. Logisim is
#   launched per solve and just needs DISPLAY to point somewhere.
set -e

Xvfb :99 -screen 0 1024x768x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
export DISPLAY=:99

# Xvfb takes a moment to create its socket, and a Logisim run that arrives
# before it exists fails with an X error that looks like a Logisim problem.
# Waiting for the socket is cheap and removes a class of confusing failure.
for _ in 1 2 3 4 5 6 7 8 9 10; do
    [ -e /tmp/.X11-unix/X99 ] && break
    sleep 0.3
done

exec python -m ohmwork.server
