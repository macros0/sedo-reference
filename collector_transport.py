"""Conservative, sequential SFR transport used by the daily XSD collector."""
import time
import threading
from urllib.error import HTTPError

REQUEST_POLICY = {"min_pause_seconds": 5, "request_timeout_seconds": 30,
                  "attempts_per_url": 1, "max_requests": 32, "parallel_requests": 1,
                  "stop_on_first_error": True}


class PoliteFetch:
    """No automatic retries; stop every seed after the first failed request."""
    def __init__(self, fetch, max_requests=32):
        self.fetch = fetch
        self.count = 0
        self.finished_at = None
        self.stopped = None
        self.max_requests = max_requests
        self.lock = threading.Lock()

    def __call__(self, url, *args, **kwargs):
        with self.lock:
            return self._request(url)

    def _request(self, url):
        if self.stopped:
            raise RuntimeError("Collection stopped after first request error: " + self.stopped)
        if self.count >= self.max_requests:
            self.stopped = "Request budget exhausted"
            raise RuntimeError(self.stopped)
        if self.finished_at is not None:
            pause = REQUEST_POLICY["min_pause_seconds"] - (time.monotonic() - self.finished_at)
            if pause > 0:
                time.sleep(pause)
        self.count += 1
        print("FETCH " + url, flush=True)
        try:
            raw = self.fetch(url, timeout=REQUEST_POLICY["request_timeout_seconds"], attempts=1)
            print("OK " + str(len(raw)) + " bytes " + url, flush=True)
            return raw
        except Exception as error:
            self.stopped = type(error).__name__ + ": " + str(error)
            if isinstance(error, HTTPError) and error.headers.get("Retry-After"):
                self.stopped += "; Retry-After=" + error.headers["Retry-After"]
            print("STOP " + self.stopped, flush=True)
            raise
        finally:
            self.finished_at = time.monotonic()
