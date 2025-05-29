#!/usr/bin/env python3

import time
import requests
import json
from collections import deque
from scipy.stats import linregress

OUTPUT_DIR = "/home/pi/ratemeter"
FILE_SHORTTERM = f"{OUTPUT_DIR}/shortterm"
FILE_SMOOTHED = f"{OUTPUT_DIR}/smoothed"
FILE_LONGTERM = f"{OUTPUT_DIR}/longterm"
FILE_MIDTERM = f"{OUTPUT_DIR}/midterm"

HOST = "http://ratos2.local"
DISTANCE_FILE = "/home/pi/tmp/beacon_rate"
INTERVAL = 1  # seconds
SAMPLES_SHORTTERM = 60  # number of samples to use for calculating a rate
NUMBER_OF_RATES = 20 #number of calculated short-term rates to average
SAMPLES_LONGTERM = 300 #number of samples to use for long-term rate
SAMPLES_MIDTERM = 120  # number of samples to use for mid-term rate

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
        print(f"Error querying distance: {e}")
        return None

def compute_rate(samples):
    if len(samples) < 2:
        return 0.0, 0

    times = [s[0] - samples[0][0] for s in samples]  # relative times
    dists = [s[1] for s in samples]

    slope, intercept, r_value, p_value, std_err = linregress(times, dists)
    print(f"SciPy slope   : {slope * 1e6:.2f} nm/s (R² = {r_value**2:.4f})")

    # # Compare to endpoint slope
    # dt = times[-1] - times[0]
    # dd = dists[-1] - dists[0]
    # endpoint_slope = dd / dt if dt else 0.0
    # print(f"Endpoint slope: {endpoint_slope * 1e6:.2f} nm/s")

    return slope, len(samples)  # rate in mm/s, number of samples used

def write_rate_to_file(file_handle, rate_in_mm_per_s):
    try:
        rate_pm_s = round(rate_in_mm_per_s * 1e9)
        rate_shifted = rate_pm_s + 100000
        rate_limited = max(-273000, min(rate_shifted, 200000))
        file_handle.seek(0)
        file_handle.write(f"{rate_limited}\n")
        file_handle.truncate()
        file_handle.flush()
    except Exception as e:
        print(f"Failed to write to file: {e}")


def main():
    with open(FILE_SHORTTERM, "w+") as f_short, \
         open(FILE_MIDTERM, "w+") as f_mid, \
         open(FILE_LONGTERM, "w+") as f_long, \
         open(FILE_SMOOTHED, "w+") as f_smooth:
        while True:
            now = time.time()
            dist = get_distance()
            if dist is not None:
                samples.append((now, dist))
                #print(f"NEW SAMPLE: {samples[-1]}")
                if len(samples) >= 5:
                    shortterm_samples = list(samples)[-SAMPLES_SHORTTERM:]
                    midterm_samples = list(samples)[-SAMPLES_MIDTERM:]
                    rate_short, count_short = compute_rate(shortterm_samples)
                    rate_mid, count_mid = compute_rate(midterm_samples)
                    rate_long, count_long = compute_rate(samples)
                    weight = count_short / SAMPLES_SHORTTERM
                    rate_samples_shortterm.append((rate_short, weight))
                    if len(rate_samples_shortterm) > 0:
                        total_weight = sum(w for _, w in rate_samples_shortterm)
                        if total_weight > 0:
                            avg_rate = sum(r * w for r, w in rate_samples_shortterm) / total_weight
                        else:
                            avg_rate = 0.0
                        print(f"shortterm rate : {rate_short * 1e6:.2f} nm/s")
                        print(f"Smoothed rate  : {avg_rate * 1e6:.2f} nm/s")
                        print(f"midterm rate   : {rate_mid * 1e6:.2f} nm/s")
                        print(f"longterm rate  : {rate_long * 1e6:.2f} nm/s")

                    write_rate_to_file(f_short, rate_short)
                    write_rate_to_file(f_mid, rate_mid)
                    write_rate_to_file(f_long, rate_long)
                    write_rate_to_file(f_smooth, avg_rate)
            time.sleep(INTERVAL)

if __name__ == "__main__":
    main()