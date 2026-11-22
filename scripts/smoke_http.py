"""Check a deployed API with bounded retries; no credentials are logged."""

import argparse
import json
import time
from urllib.error import URLError
from urllib.request import Request, urlopen


def smoke(url: str, attempts: int = 12) -> bool:
    """Require a healthy response and a successful document question."""
    for attempt in range(attempts):
        try:
            with urlopen(url.rstrip("/") + "/health", timeout=20) as response:
                if json.load(response) != {"status": "ok"}:
                    raise ValueError("Unexpected health payload")
            request = Request(
                url.rstrip("/") + "/ask",
                data=json.dumps({"question": "What is the annual leave policy?"}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urlopen(request, timeout=60) as response:
                body = json.load(response)
                if response.status != 200 or body.get("route") != "doc" or not body.get("answer"):
                    raise ValueError("Unexpected document response")
            return True
        except (URLError, TimeoutError, ValueError):
            if attempt + 1 < attempts:
                time.sleep(min(2**attempt, 20))
    return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    args = parser.parse_args()
    success = smoke(args.url)
    print("Deployed API smoke test: " + ("PASS" if success else "FAIL; check Container App logs"))
    raise SystemExit(0 if success else 1)
