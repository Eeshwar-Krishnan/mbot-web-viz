import math
import random
import signal
import sys
import time

from DataLogger2 import dataLogger


def run_demo(num_channels: int = 3, hz: float = 60.0) -> None:
    """Start the realtime server and publish demo data rows at `hz`.

    This demo exercises:
    - time-domain plots
    - FFT mode (sinusoidal channels)
    - gyro visualizer (gyro_x/gyro_y in degrees)
    - PID mode (pid + setpoint)

    The server prepends a `__time_start` column automatically.
    """

    dl = dataLogger("test.txt")

    headers = [f"ch{k+1}" for k in range(num_channels)] + ["gyro_x", "gyro_y", "pid", "setpoint"]
    dl.appendHeader(headers)
    dl.addGyroXY("gyro_x", "gyro_y")
    dl.addPidTuning("pid", "setpoint")

    print("Demo running. Open http://localhost:8765 (or the Pi's IP). Press Ctrl+C to stop.")

    # seed random walks for ch*
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
        row = []

        for k in range(num_channels):
            # random walk + slow sine component
            base[k] += random.uniform(-0.05, 0.05)
            val = base[k] + amp[k] * math.sin(0.7 * t + phase[k]) + drift[k] * t * 0.02
            row.append(round(val, 4))

        # gyro in degrees (roughly -90..90)
        gyro_x = 90.0 * math.sin(0.6 * t)
        gyro_y = 90.0 * math.sin(0.4 * t + 0.7)
        row.append(round(gyro_x, 3))
        row.append(round(gyro_y, 3))

        # PID + setpoint demo
        setpoint = 25.0 * math.sin(0.25 * t)
        pid = setpoint + 5.0 * math.sin(1.7 * t) + random.uniform(-1.0, 1.0)
        row.append(round(pid, 3))
        row.append(round(setpoint, 3))

        dl.appendData(row)
        i += 1
        # sleep to match rate
        target = start + i * period
        now = time.time()
        delay = max(0.0, target - now)
        time.sleep(delay)

    dl.stop()


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


