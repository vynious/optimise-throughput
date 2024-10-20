import time
import contextlib
import asyncio


from statistics import mean
from collections import deque


def timestamp_ms() -> int:
    return int(time.time() * 1000)

class RateLimiterTimeout(Exception):
    """Custom exception for rate limiter timeouts."""
    pass

class RateLimiter:
    """Controls the rate of requests sent to the server."""
    def __init__(self, per_second_rate: int, min_duration_ms: int):
        self.__per_second_rate = per_second_rate
        self.__min_duration_ms = min_duration_ms
        self.__request_times = [0] * per_second_rate
        self.__curr_idx = 0

        # Adaptive latency buffer
        self.__latency_window = deque(maxlen=100)
        self.__buffer = 40  # Initial buffer (ms)
        self.__min_buffer = 30  # Min buffer (ms)
        self.__max_buffer = 50  # Max buffer (ms)

    def update_buffer(self):
        """Update the buffer based on recent latencies."""
        if self.__latency_window:
            avg_latency = mean(self.__latency_window)
            self.__buffer = min(self.__max_buffer, max(self.__min_buffer, int(avg_latency * 1.1)))

    def record_latency(self, latency: int):
        """Record the latency of a request."""
        self.__latency_window.append(latency)
        self.update_buffer()

    @contextlib.asynccontextmanager
    async def acquire(self, timeout_ms: int = 0):
        """Ensure the rate limit is respected."""
        enter_ms = timestamp_ms()
        buffer = self.__buffer
        initial_buffer = self.__min_duration_ms * self.__per_second_rate

        while True:
            now = timestamp_ms()
            if now - enter_ms > timeout_ms > 0:
                raise RateLimiterTimeout()

            if now - self.__request_times[self.__curr_idx] <= initial_buffer + buffer:
                sleep_time = (initial_buffer + buffer - (now - self.__request_times[self.__curr_idx])) / 1000
                await asyncio.sleep(sleep_time)
                continue

            break

        self.__request_times[self.__curr_idx] = now
        self.__curr_idx = (self.__curr_idx + 1) % self.__per_second_rate
        yield