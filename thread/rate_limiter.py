import contextlib
import threading
import time
from collections import deque

def timestamp_ms() -> int:
    return int(time.time() * 1000)

class RateLimiterTimeout(Exception):
    """Custom exception for rate limiter timeouts."""
    pass

class ThreadSafeRateLimiter:
    """Controls the rate of requests sent to the server."""
    def __init__(self, per_second_rate, min_duration_ms_between_requests):
        self.per_second_rate = per_second_rate
        self.request_times = [0] * per_second_rate
        self.min_duration_ms_between_requests = min_duration_ms_between_requests
        self.curr_idx = 0
        self.lock = threading.Lock()
        
        self.latency_window = deque(maxlen=100)  # record of the last 100 latencies
        self.buffer = 40  # initial buffer (ms)
        self.min_buffer = 30  # min buffer (ms)
        self.max_buffer = 50  # max buffer (ms)
        
        
    def update_buffer(self):
        """Update the buffer based on recent latencies."""
        if len(self.latency_window) > 0:
            avg_latency = sum(self.latency_window) / len(self.latency_window)
            # adjust buffer based on average latency
            self.buffer = min(self.max_buffer, max(self.min_buffer, int(avg_latency * 1.1)))

    def record_latency(self, latency):
        """Record the latency of a request."""
        self.latency_window.append(latency)
        self.update_buffer()

    @contextlib.contextmanager
    def acquire(self, timeout_ms=0):
        """Ensure the rate limit is respected."""
        enter_ms = timestamp_ms()
        buffer = self.buffer
        initial_buffer = self.min_duration_ms_between_requests * self.per_second_rate
        
        while True:
            now = timestamp_ms()
            if now - enter_ms > timeout_ms > 0:
                raise RateLimiterTimeout()

            # sleep the exact remaining time to the next second
            sleep_time = (initial_buffer + buffer - (now - self.request_times[self.curr_idx])) / 1000
            
            with self.lock:
                if now - self.request_times[self.curr_idx] >= initial_buffer + buffer:
                    self.request_times[self.curr_idx] = now
                    self.curr_idx = (self.curr_idx + 1) % self.per_second_rate
                    yield
                    return

            time.sleep(sleep_time)