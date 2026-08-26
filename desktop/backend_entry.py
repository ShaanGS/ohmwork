"""Entry point frozen into the desktop backend executable.

It must stay this small. The actual application is `ohmwork.server`, shared
with the web and CLI paths; the desktop application is a shell, not another
solver implementation free to drift from the one Logisim and LTspice verify.
"""

from ohmwork.server import main


if __name__ == "__main__":
    raise SystemExit(main())
