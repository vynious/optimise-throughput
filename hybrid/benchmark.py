import time
import logging
import asyncio
from statistics import mean


def timestamp_ms() -> int:
    return int(time.time() * 1000)

class Benchmark:
    """Tracks metrics and logs performance data."""
    def __init__(self, logger: logging.Logger):
        self.start_time = timestamp_ms()
        self.logger = logger
        self.total_successful_requests = 0
        self.total_failed_requests = 0
        self.total_latencies = []

    def record_success(self, latency_ms: int):
        """Record a successful request and its latency."""
        self.total_successful_requests += 1
        self.total_latencies.append(latency_ms)

    def record_failure(self):
        """Record a failed request."""
        self.total_failed_requests += 1

    def print_metrics(self):
        """Print accumulated metrics."""
        elapsed = (timestamp_ms() - self.start_time) / 1000
        avg_latency = mean(self.total_latencies) if self.total_latencies else 0
        throughput = self.total_successful_requests / elapsed if elapsed > 0 else 0

        self.logger.info("\n--- Accumulated Benchmark Metrics ---")
        self.logger.info(f"Elapsed Time: {elapsed:.2f} seconds")
        self.logger.info(f"Total Successful Requests: {self.total_successful_requests}")
        self.logger.info(f"Total Failed Requests: {self.total_failed_requests}")
        self.logger.info(f"Total Throughput: {throughput:.2f} req/sec")
        self.logger.info(f"Average Latency: {avg_latency:.2f} ms")
        

    async def metrics_printer(self, interval: int = 5):
        """Print metrics at regular intervals."""
        while True:
            await asyncio.sleep(interval)
            self.print_metrics()