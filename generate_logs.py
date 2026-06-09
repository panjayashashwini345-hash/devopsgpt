import requests
import random
import time
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TOKEN = "f8afc7f5-33eb-49da-9471-925956a946c5"
URL = "https://localhost:8088/services/collector/event"

print("🚀 Starting log generation...")

for i in range(800):   # Generate 800 realistic events
    latency = random.randint(80, 6500)
    error = None
    status = 200

    if latency > 2800:
        status = 503
        error = random.choice([
            "N+1 query detected load_order_items",
            "database connection timeout",
            "payment gateway unavailable",
            "order service failed"
        ])

    event = {
        "event": {
            "service": "checkout-service",
            "endpoint": "/api/checkout",
            "deployment": random.choice(["v2.3.0", "v2.4.0", "v2.4.1"]),
            "commit": "9f3c1a2" if i % 3 == 0 else "a1b2c3d",
            "latency_ms": latency,
            "status": status,
            "error": error,
            "user_count": random.randint(50, 1200)
        },
        "source": "devopsgpt-demo",
        "sourcetype": "checkout-service"
    }

    try:
        r = requests.post(
            URL,
            headers={"Authorization": f"Splunk {TOKEN}"},
            json=event,
            verify=False,
            timeout=10
        )
        print(f"sent {i:3d} | latency={latency:4d}ms | status={status} | {'ERROR' if error else 'OK'}")
    except Exception as e:
        print(f"❌ Error on {i}: {e}")

    time.sleep(0.12)

print("✅ Log generation completed!")