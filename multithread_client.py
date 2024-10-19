import sys
import time
import random
import logging
import contextlib

import threading
from queue import Queue
import requests
import asyncio
from statistics import mean


# region: DO NOT CHANGE - the code within this region can be assumed to be "correct"
PER_SEC_RATE = 20
DURATION_MS_BETWEEN_REQUESTS = int(1000 / PER_SEC_RATE)
REQUEST_TTL_MS = 1000
VALID_API_KEYS = [
    'UT4NHL1J796WCHULA1750MXYF9F5JYA6',
    '8TY2F3KIL38T741G1UCBMCAQ75XU9F5O',
    '954IXKJN28CBDKHSKHURQIVLQHZIEEM9',
    'EUU46ID478HOO7GOXFASKPOZ9P91XGYS',
    '46V5EZ5K2DFAGW85J18L50SGO25WJ5JE'
]


async def generate_requests(queue: Queue):
    """
    co-routine responsible for generating requests
    """
    curr_req_id = 0
    MAX_SLEEP_MS = int(1000 / PER_SEC_RATE / len(VALID_API_KEYS) * 1.05 * 2.0)
    while True:
        queue.put_nowait(Request(curr_req_id))
        curr_req_id += 1
        sleep_ms = random.randint(0, MAX_SLEEP_MS)
        await asyncio.sleep(sleep_ms / 1000.0)


def timestamp_ms() -> int:
    return int(time.time() * 1000)

# endregion

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

def metrics_printer(benchmark: Benchmark, interval=5):
    while True:
        time.sleep(interval)
        benchmark.print_metrics()


def configure_logger(name=None):
    logger = logging.getLogger(name)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(formatter)
    logger.addHandler(sh)

    if name == "debug":
        fh = logging.FileHandler("async-debug.log", mode="a")
        fh.setFormatter(formatter)
        logger.addHandler(fh)

    elif name == "stats":
        fh = logging.FileHandler("status.log", mode="a")
        fh.setFormatter(formatter)
        logger.addHandler(fh)

    logger.setLevel(logging.DEBUG)
    return logger

class RateLimiterTimeout(Exception):
    pass

class Request:
    def __init__(self, req_id, retry_count=0):
        self.req_id = req_id
        self.create_time = timestamp_ms()
        self.retry_count = retry_count

    def update_create_time(self):
        self.create_time = timestamp_ms()

thread_local = threading.local()

def get_unique_nonce():
    if not hasattr(thread_local, 'nonce_counter'):
        thread_local.nonce_counter = 0
    thread_local.nonce_counter += 1
    timestamp = timestamp_ms()
    thread_id = threading.get_ident()
    # Concatenate values to form a unique nonce
    nonce_str = f"{timestamp}{thread_id}{thread_local.nonce_counter}"
    # Convert to integer if needed
    return int(nonce_str)

from collections import deque

class ThreadSafeRateLimiter:
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
        # calculate a moving average of the recent latencies
        if len(self.latency_window) > 0:
            avg_latency = sum(self.latency_window) / len(self.latency_window)
            # adjust buffer based on average latency
            self.buffer = min(self.max_buffer, max(self.min_buffer, int(avg_latency * 1.1)))

    def record_latency(self, latency):
        self.latency_window.append(latency)
        self.update_buffer()

    @contextlib.contextmanager
    def acquire(self, timeout_ms=0):
        enter_ms = timestamp_ms()
        buffer = self.buffer
        initial_buffer = self.min_duration_ms_between_requests * self.per_second_rate
        
        while True:
            now = timestamp_ms()
            if now - enter_ms > timeout_ms > 0:
                raise RateLimiterTimeout()

            sleep_time = (initial_buffer + buffer - (now - self.request_times[self.curr_idx])) / 1000
            
            with self.lock:
                if now - self.request_times[self.curr_idx] > initial_buffer + buffer:
                    self.request_times[self.curr_idx] = now
                    self.curr_idx = (self.curr_idx + 1) % self.per_second_rate
                    yield
                    return

            time.sleep(sleep_time)

class ThreadSafeQueue(Queue):
    pass  # thread safety from queue.Queue


class QueueManager:
    def __init__(self, main_queue: ThreadSafeQueue, dlq_queue: ThreadSafeQueue):
        self.main_queue = main_queue
        self.dlq_queue = dlq_queue
        self.max_retry_count = 3
        self.graveyard = set()
        self.lock = threading.Lock()

    def add_request(self, request: Request):
        """Add a new request to the main queue."""
        with self.lock:
            self.main_queue.put(request)

    def requeue_from_dlq(self, benchmark: Benchmark):
        """Requeue requests from DLQ to the main queue with priority."""
        while True:
            request: Request = self.dlq_queue.get()
            if request.retry_count >= self.max_retry_count:
                self.graveyard.add(request.req_id)
                print(f"Request {request.req_id} moved to graveyard.")
                benchmark.record_failure()
            else:
                request.retry_count += 1
                request.update_create_time()
                with self.lock:
                    self.main_queue.put(request)
                    
            self.dlq_queue.task_done()

    def add_to_dlq(self, request: Request):
        """Add a failed request to the DLQ."""
        with self.lock:
            self.dlq_queue.put(request)
    
    def get_from_main(self):
        """Get a request from the main queue."""
        return self.main_queue.get()


# same as non-threadsafe version but without asyncio.sleep
def generate_requests_threadsafe(queue: ThreadSafeQueue):
    curr_req_id = 0
    MAX_SLEEP_MS = int(1000 / PER_SEC_RATE / len(VALID_API_KEYS) * 1.05 * 2.0)
    while True:
        queue.put_nowait(Request(curr_req_id))
        curr_req_id += 1
        sleep_ms = random.randint(0, MAX_SLEEP_MS)
        time.sleep(sleep_ms / 1000.0)


def exchange_worker(api_key, queue_manager: QueueManager, rate_limiter: ThreadSafeRateLimiter, logger, benchmark: Benchmark):
    """Process requests in a thread-safe way."""
    session = requests.Session()
    while True:
        request: Request = queue_manager.get_from_main()
        remaining_ttl = REQUEST_TTL_MS - (timestamp_ms() - request.create_time)

        if remaining_ttl <= 0:
            queue_manager.add_to_dlq(request)
            queue_manager.main_queue.task_done()
            continue

        try:
            with rate_limiter.acquire(timeout_ms=remaining_ttl):
                try:
                    data = {'api_key': api_key, 'nonce': get_unique_nonce(), 'req_id': request.req_id}
                    response = session.get("http://127.0.0.1:9999/api/request", params=data, timeout=1.0)
                    json_resp = response.json()
                    latency = timestamp_ms() - request.create_time
                    rate_limiter.record_latency(latency)
                    if json_resp['status'] == 'OK':
                        logger.info(f"API response: status {json_resp['status']}, resp {json_resp}")
                        benchmark.record_success(latency)
                    else:
                        logger.warning(f"API response: status {json_resp['status']}, resp {json_resp}")
                except (requests.Timeout, requests.RequestException):
                    queue_manager.add_to_dlq(request)
        except RateLimiterTimeout:
            logger.warning(f"Timeout for request {request.req_id}")
            queue_manager.add_to_dlq(request)
        finally:
            queue_manager.main_queue.task_done()

def main():
    logger = configure_logger("debug")
    benchmark_logger = configure_logger("stats")
    
    main_queue = ThreadSafeQueue()
    dlq_queue = ThreadSafeQueue()
    
    queue_manager = QueueManager(main_queue=main_queue, dlq_queue=dlq_queue)
    benchmark = Benchmark(benchmark_logger)

    # rate limiter per API key
    rate_limiters = {key: ThreadSafeRateLimiter(PER_SEC_RATE, DURATION_MS_BETWEEN_REQUESTS) for key in VALID_API_KEYS}

    # request generator in a thread
    request_generator_thread = threading.Thread(
        target=generate_requests_threadsafe,
        args=(main_queue,),
        daemon=True
    )
    request_generator_thread.start()

    # DLQ requeue task in a thread
    dlq_requeue_thread = threading.Thread(
        target=queue_manager.requeue_from_dlq,
        daemon=True
    )
    dlq_requeue_thread.start()

    # metrics printer in a thread
    metrics_thread = threading.Thread(
        target=metrics_printer,
        args=(benchmark,),
        daemon=True
    )
    metrics_thread.start()


    # Create and start worker threads
    threads = []
    for api_key in VALID_API_KEYS:
        t = threading.Thread(
            target=exchange_worker,
            args=(api_key, queue_manager, rate_limiters[api_key], logger, benchmark),
            daemon=True
        )
        t.start()
        threads.append(t)


    # Keep the main thread running
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass

if __name__ == '__main__':
    main()