# Copyright (c) 2026 The Jaeger Authors.
# SPDX-License-Identifier: Apache-2.0

"""HotROD trace generator: standard library only, tuned through the environment."""

import os
import random
import time
import urllib.parse
import urllib.request

TARGET_URL = os.environ["TARGET_URL"]
SLEEP_SECONDS = float(os.environ["SLEEP_SECONDS"])

CUSTOMER_IDS = [123, 392, 731, 567]

print(f"Starting HotROD load generator → {TARGET_URL} (interval={SLEEP_SECONDS}s)", flush=True)

i = 0
while True:
    query = urllib.parse.urlencode({"customer": random.choice(CUSTOMER_IDS), "nonse": random.random()})
    url = f"{TARGET_URL}?{query}"
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            print(f"[{i}] Sent to {url} → {response.status}", flush=True)
    except Exception as error:
        print(f"[{i}] Error: {error}", flush=True)
    i += 1
    time.sleep(SLEEP_SECONDS)
