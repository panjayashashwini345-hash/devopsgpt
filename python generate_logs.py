import os

TOKEN = os.getenv("SPLUNK_HEC_TOKEN")

import requests
import random
import time
import urllib3


urllib3.disable_warnings()


TOKEN = "f8afc7f5-33eb-49da-9471-925956a946c5"

URL = "https://localhost:8088/services/collector/event"

logs = [
    "INFO checkout request started",
    "INFO payment processing",
    "WARN checkout latency 2500ms",
    "WARN checkout latency 3200ms",
    "ERROR database connection timeout",
    "ERROR payment gateway unavailable",
    "INFO deployment v2.4.0 completed",
    "ERROR order service failed",
    "WARN memory usage 90%",
    "ERROR N+1 query detected load_order_items"
]

for i in range(100):
    payload = {
        "event": random.choice(logs),
        "source": "devopsgpt-demo",
        "sourcetype": "checkout-service"
    }

    r = requests.post(
        URL,
        headers={"Authorization": f"Splunk {TOKEN}"},
        json=payload,
        verify=False
    )

    print(i, r.status_code, r.text)

    time.sleep(0.1)