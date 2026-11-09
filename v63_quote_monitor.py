"""Experimental rate-paced Jupiter quote adapter. No transactions or private keys."""
import time
import requests
import radar_quote_monitor as base

class QuotePacer:
    """Local process pacing and bounded backoff. No shared/global quota guarantee."""
    def __init__(self, min_interval=1.5, retries=2, clock=None, sleep=None):
        self.min_interval = min_interval
        self.retries = retries
        self.clock = clock or time.monotonic
        self.sleep = sleep or time.sleep
        self.last_attempt = None

    def call(self, fn, *args, **kwargs):
        for attempt in range(self.retries + 1):
            if self.last_attempt is not None:
                delay = self.min_interval - (self.clock() - self.last_attempt)
                if delay > 0:
                    self.sleep(delay)
            self.last_attempt = self.clock()
            try:
                return fn(*args, **kwargs)
            except requests.HTTPError as error:
                response = getattr(error, "response", None)
                status = getattr(response, "status_code", None)
                if status != 429 or attempt >= self.retries:
                    raise
                retry_after = None
                if response is not None:
                    raw = response.headers.get("Retry-After")
                    try:
                        retry_after = float(raw)
                    except (TypeError, ValueError):
                        pass
                self.sleep(max(2 ** (attempt + 1), min(max(retry_after or 0, 0), 30)))

def main():
    original = base.quote
    pacer = QuotePacer()
    def paced_quote(*args, **kwargs):
        return pacer.call(original, *args, **kwargs)
    base.quote = paced_quote
    try:
        base.main()
    finally:
        base.quote = original

if __name__ == "__main__":
    main()
