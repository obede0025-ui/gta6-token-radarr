"""Bounded Jupiter GET quote pacing and retries; never submits transactions.

One shared limiter within a Python process. Different jobs/processes do not
share a limiter; no claim of cross-process rate limit enforcement.
"""
import math
import time
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone

import requests

MIN_INTERVAL_SECONDS = 2.5
MAX_ATTEMPTS = 3
MAX_BACKOFF_SECONDS = 12.0
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

class JupiterPacer:
    def __init__(self, interval=MIN_INTERVAL_SECONDS, clock=None, sleep=None):
        if not 0 < interval <= 30:
            raise ValueError("invalid quote pacing interval")
        self.interval = interval
        self.clock = clock or time.monotonic
        self.sleep = sleep or time.sleep
        self.last_request = None
        self.total_requests = 0
        self.total_retries = 0
        self.rate_limits = 0

    def pace(self):
        if self.last_request is not None:
            wait = self.interval - (self.clock() - self.last_request)
            if wait > 0:
                self.sleep(wait)
        self.last_request = self.clock()
        self.total_requests += 1

    def backoff(self, response, retry_number):
        # "Retry-After" may be seconds or HTTP-date. No unbounded sleep.
        candidate = None
        header = (response.headers or {}).get("Retry-After") if response is not None else None
        if header is not None:
            try:
                value=float(header)
                if math.isfinite(value) and value >= 0: candidate=value
            except (ValueError, TypeError):
                try:
                    date=parsedate_to_datetime(header)
                    if date.tzinfo is None:date=date.replace(tzinfo=timezone.utc)
                    candidate=max(0,(date-datetime.now(timezone.utc)).total_seconds())
                except (ValueError, TypeError, OverflowError, IndexError):pass
        default=min(MAX_BACKOFF_SECONDS,2**retry_number)
        self.sleep(min(MAX_BACKOFF_SECONDS,max(default,candidate or 0)))

    def get(self, session, url, **kwargs):
        for attempt in range(MAX_ATTEMPTS):
            self.pace()
            try:
                response = session.get(url,**kwargs)
            except (requests.Timeout, requests.ConnectionError):
                if attempt + 1 == MAX_ATTEMPTS:raise
                self.total_retries += 1
                self.backoff(None,attempt+1)
                continue
            if response.status_code == 429:self.rate_limits += 1
            if response.status_code in RETRYABLE_STATUS and attempt + 1 < MAX_ATTEMPTS:
                self.total_retries += 1
                self.backoff(response,attempt+1)
                continue
            response.raise_for_status()
            return response
        raise RuntimeError("Jupiter GET retry exhausted")

SHARED_PACER = JupiterPacer()
