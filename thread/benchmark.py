

import threading
import time
import logging
from statistics import mean

def timestamp_ms() -> int:
    return int(time.time() * 1000)

class Benchmark:
    def __init__(self, logger: logging.Logger):
        self.start_time = timestamp_ms()
        self.total_successful_requests = 0
        self.total_failed_requests = 0
        self.total_latencies = []
        self.interval_successful_requests = 0
        self.interval_failed_requests = 0
        self.interval_latencies = []
        self.logger = logger
        self.lock = threading.Lock()

    def record_success(self, latency_ms):
        with self.lock:
            self.total_successful_requests += 1
            self.total_latencies.append(latency_ms)
            self.interval_successful_requests += 1
            self.interval_latencies.append(latency_ms)

    def record_failure(self):
        with self.lock:
            self.total_failed_requests += 1
            self.interval_failed_requests += 1

    def print_metrics(self):
        with self.lock:
            elapsed = (timestamp_ms() - self.start_time) / 1000
            avg_latency = mean(self.total_latencies) if self.total_latencies else 0
            total_throughput = self.total_successful_requests / elapsed if elapsed > 0 else 0
            self.logger.info("\n--- Accumulated Benchmark Metrics ---")
            self.logger.info(f"Elapsed Time: {elapsed:.2f} seconds")
            self.logger.info(f"Total Successful Requests: {self.total_successful_requests}")
            self.logger.info(f"Total Failed Requests: {self.total_failed_requests}")
            self.logger.info(f"Total Throughput: {total_throughput:.2f} req/sec")
            self.logger.info(f"Average Latency (Total): {avg_latency:.2f} ms")

            self.interval_successful_requests = 0
            self.interval_failed_requests = 0
            self.interval_latencies = []

    def metrics_printer(self, interval=5):
        while True:
            time.sleep(interval)
            self.print_metrics()

