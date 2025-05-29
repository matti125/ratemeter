#!/usr/bin/env python3

from statistics import mean
import urllib.parse
import time
import requests
import sys
import json
from collections import deque
from scipy.stats import linregress
import argparse

OUTPUT_DIR = "/home/pi/ratemeter"
FILE_SHORTTERM = f"{OUTPUT_DIR}/shortterm"
FILE_SMOOTHED = f"{OUTPUT_DIR}/smoothed"
FILE_LONGTERM = f"{OUTPUT_DIR}/longterm"
FILE_MIDTERM = f"{OUTPUT_DIR}/midterm"

HOST = "http://ratos2.local"
INFLUX_HELPER = "/home/pi/devs/zhopper/influx_write_by_line.py"
INFLUX_BUCKET = "gantry"
INFLUX_MEASUREMENT = "gantry"
INTERVAL = 1  # seconds
SAMPLES_SHORTTERM = 60  # number of samples to use for calculating a rate
NUMBER_OF_RATES = 20 #number of calculated short-term rates to average
SAMPLES_MIDTERM = 120  # number of samples to use for mid-term rate
SAMPLES_LONGTERM = 300 #number of samples to use for long-term rate

import subprocess

# Rolling buffer of (timestamp, distance) tuples
samples = deque(maxlen=SAMPLES_LONGTERM)
rate_samples_shortterm = deque(maxlen=NUMBER_OF_RATES)  # store (rate, weight)




def get_distance():
    try:
        resp = requests.get(f"{HOST}/printer/objects/query?beacon", timeout=2)
        resp.raise_for_status()
        data = resp.json()
        sample = data["result"]["status"]["beacon"].get("last_received_sample")
        if not sample or "dist" not in sample:
            raise KeyError("dist not in last_received_sample")
        return float(sample["dist"])
    except Exception as e:
        print(f"Error querying distance: {e}", file=sys.stderr)
        return None

def compute_rate(samples):
    if len(samples) < 2:
        return 0.0, 0, 0.0

    times = [s[0] - samples[0][0] for s in samples]  # relative times
    dists = [s[1] for s in samples]

    slope, intercept, r_value, p_value, std_err = linregress(times, dists)
#    print(f"SciPy slope   : {slope * 1e6:.2f} nm/s (R² = {r_value**2:.4f})")

    # # Compare to endpoint slope
    # dt = times[-1] - times[0]
    # dd = dists[-1] - dists[0]
    # endpoint_slope = dd / dt if dt else 0.0
    # print(f"Endpoint slope: {endpoint_slope * 1e6:.2f} nm/s")

    return slope, len(samples), r_value  # rate in mm/s, number of samples used, r_value

def write_rate_to_file(file_handle, rate_in_mm_per_s):
    try:
        rate_pm_s = round(rate_in_mm_per_s * 1e9)
        rate_shifted = rate_pm_s + 100000
        rate_limited = max(-273000, min(rate_shifted, 200000))
        file_handle.seek(0)
        output = f"{rate_limited:9d}\n"
        file_handle.write(output)
        file_handle.flush()
    except Exception as e:
        print(f"Failed to write to file: {e}", file=sys.stderr)

def parse_args():
    parser = argparse.ArgumentParser(description="Ratemeter daemon")
    parser.add_argument("--log", action="store_true", help="Print log line with rates and details")
    return parser.parse_args()

def main(args):
    with open(FILE_SHORTTERM, "w+") as f_short, \
         open(FILE_MIDTERM, "w+") as f_mid, \
         open(FILE_LONGTERM, "w+") as f_long, \
         open(FILE_SMOOTHED, "w+") as f_smooth:
        recent_dists = deque(maxlen=5)
        averaged_dists = []
        while True:
            now = time.time()
            dist = get_distance()
            if dist is not None:
                samples.append((now, dist))
                recent_dists.append(dist)
                if len(recent_dists) == 5:
                    dist_avg = mean(recent_dists)
                    line = f"{INFLUX_MEASUREMENT} distance={dist_avg:.6f} {int(now * 1e9)}"
                    try:
                        subprocess.run(
                            ["python3", INFLUX_HELPER, "--bucket", INFLUX_BUCKET],
                            input=line.encode("utf-8"),
                            check=True
                        )
                    except Exception as e:
                        print(f"Error calling influx_write_by_line.py: {e}", file=sys.stderr)
                    recent_dists.clear()
                if len(samples) >= 5:
                    shortterm_samples = list(samples)[-SAMPLES_SHORTTERM:]
                    midterm_samples = list(samples)[-SAMPLES_MIDTERM:]
                    rate_short, count_short, r_short = compute_rate(shortterm_samples)
                    rate_mid, count_mid, r_mid = compute_rate(midterm_samples)
                    rate_long, count_long, r_long = compute_rate(samples)
                    weight = count_short / SAMPLES_SHORTTERM
                    rate_samples_shortterm.append((rate_short, weight))
                    if len(rate_samples_shortterm) > 0:
                        total_weight = sum(w for _, w in rate_samples_shortterm)
                        if total_weight > 0:
                            avg_rate = sum(r * w for r, w in rate_samples_shortterm) / total_weight
                        else:
                            avg_rate = 0.0
                        if args.log:
                            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} dist={dist:.6f} "
                                  f"rate_short={rate_short*1e6:.2f} r2_short={r_short**2:.4f} "
                                  f"rate_mid={rate_mid*1e6:.2f} r2_mid={r_mid**2:.4f} "
                                  f"rate_long={rate_long*1e6:.2f} r2_long={r_long**2:.4f} "
                                  f"avg_rate={avg_rate*1e6:.2f} nm/s")

                    write_rate_to_file(f_short, rate_short)
                    write_rate_to_file(f_mid, rate_mid)
                    write_rate_to_file(f_long, rate_long)
                    write_rate_to_file(f_smooth, avg_rate)
            time.sleep(INTERVAL)

if __name__ == "__main__":
    args = parse_args()
    main(args)