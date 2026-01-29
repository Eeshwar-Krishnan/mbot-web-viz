import math
import random
import signal
import sys
import time
from pathlib import Path

from DataLogger2 import dataLogger


def run_demo(num_channels: int = 3, hz: float = 60.0) -> None:
    """Start the realtime server and publish random data rows at `hz`.

    - First column is time (seconds since start)
    - Next columns are channels ch1..chN
    """
    web_dir = Path(__file__).resolve().parents[1] / "web"
    dl = dataLogger("test.txt")

    dl.appendData("time dpsi_x dpsi_y dpsi_z")

    print("Demo running. Open http://localhost:8765 (or the Pi's IP). Press Ctrl+C to stop.")

    # seed random walks
    base = [0.0 for _ in range(num_channels)]
    drift = [random.uniform(-0.2, 0.2) for _ in range(num_channels)]
    amp = [random.uniform(0.5, 2.5) for _ in range(num_channels)]
    phase = [random.uniform(0, math.tau) for _ in range(num_channels)]

    start = time.time()
    period = 1.0 / hz

    running = True

    def handle_sigint(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, handle_sigint)

    i = 0
    while running:
        t = time.time() - start
        row = [t]
        for k in range(num_channels):
            # random walk + slow sine component
            base[k] += random.uniform(-0.05, 0.05)
            val = base[k] + amp[k] * math.sin(0.7 * t + phase[k]) + drift[k] * t * 0.02
            row.append(round(val, 4))
        dl.appendData(row)
        i += 1
        # sleep to match rate
        target = start + i * period
        now = time.time()
        delay = max(0.0, target - now)
        time.sleep(delay)

    srv.stop()


if __name__ == "__main__":
    # Allow optional CLI args: num_channels hz
    n = 3
    rate = 20.0
    if len(sys.argv) >= 2:
        try:
            n = int(sys.argv[1])
        except Exception:
            pass
    if len(sys.argv) >= 3:
        try:
            rate = float(sys.argv[2])
        except Exception:
            pass
    run_demo(n, rate)


