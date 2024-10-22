import sys
import time
import random
import logging
import contextlib
import asyncio
import aiohttp
import async_timeout

from asyncio import Queue
from statistics import mean
from collections import deque

# region: DO NOT CHANGE - the code within this region can be assumed to be "correct"

PER_SEC_RATE = 20
DURATION_MS_BETWEEN_REQUESTS = int(1000 / PER_SEC_RATE)
REQUEST_TTL_MS = 1000
VALID_API_KEYS = [
    'UT4NHL1J796WCHULA1750MXYF9F5JYA6',
    '8TY2F3KIL38T741G1UCBMCAQ75XU9F5O',
    '954IXKJN28CBDKHSKHURQIVLQHZIEEM9',
    'EUU46ID478HOO7GOXFASKPOZ9P91XGYS',
    '46V5EZ5K2DFAGW85J18L50SGO25WJ5JE',
    ]


async def generate_requests(queue: Queue):
    """
    co-routine responsible for generating requests
    """
    curr_req_id = 0
    MAX_SLEEP_MS = 1000 / PER_SEC_RATE / len(VALID_API_KEYS) * 1.05 * 2.0
    while True:
        queue.put_nowait(Request(curr_req_id))
        curr_req_id += 1
        sleep_ms = random.randint(0, MAX_SLEEP_MS)
        await asyncio.sleep(sleep_ms / 1000.0)


def timestamp_ms() -> int:
    return int(time.time() * 1000)

# endregion

# Benchmark class tracks key metrics: success rates, latencies, and throughput.
class Benchmark:
    def __init__(self, logger: logging.Logger):
        self.start_time = timestamp_ms()
        self.logger = logger
        self.total_successful_requests = 0
        self.total_failed_requests = 0
        self.total_latencies = []  # Collect latencies for performance tracking.

    # Record successful requests with latency to monitor system responsiveness.
    def record_success(self, latency_ms: int):
        self.total_successful_requests += 1
        self.total_latencies.append(latency_ms)

    # Track failed requests for debugging and queue management.
    def record_failure(self):
        self.total_failed_requests += 1

    # Logs the performance metrics at regular intervals.
    def print_metrics(self):
        elapsed = (timestamp_ms() - self.start_time) / 1000
        avg_latency = mean(self.total_latencies) if self.total_latencies else 0
        throughput = self.total_successful_requests / elapsed if elapsed > 0 else 0

        self.logger.info("\n--- Accumulated Benchmark Metrics ---")
        self.logger.info(f"Elapsed Time: {elapsed:.2f} seconds")
        self.logger.info(f"Total Successful Requests: {self.total_successful_requests}")
        self.logger.info(f"Total Failed Requests: {self.total_failed_requests}")
        self.logger.info(f"Total Throughput: {throughput:.2f} req/sec")
        self.logger.info(f"Average Latency: {avg_latency:.2f} ms")

    # Prints metrics periodically to give real-time feedback on system health.
    async def metrics_printer(self, interval: int = 5):
        while True:
            await asyncio.sleep(interval)
            self.print_metrics()


def configure_logger(name=None) -> logging.Logger:
    logger = logging.getLogger(name)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    file_name = "async-debug.log" if name == "debug" else "async-status.log"
    file_handler = logging.FileHandler(file_name, mode="a")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    logger.setLevel(logging.DEBUG)
    return logger

# Request class encapsulates a request, including retry logic and creation timestamp.
class Request:
    def __init__(self, req_id: int, retry_count: int = 0):
        self.req_id = req_id
        self.retry_count = retry_count
        self.create_time = timestamp_ms()  # Timestamp to track request lifecycle.

    def update_create_time(self):
        self.create_time = timestamp_ms()  # Refresh timestamp for retries.

class RateLimiterTimeout(Exception):
    """Custom exception for rate limiter timeouts."""
    pass

# RateLimiter ensures compliance with rate limits using adaptive buffering for latency.
class RateLimiter:
    def __init__(self, per_second_rate: int, min_duration_ms: int):
        self.__per_second_rate = per_second_rate
        self.__min_duration_ms = min_duration_ms
        self.__request_times = [0] * per_second_rate
        self.__curr_idx = 0

        # Adaptive buffer to handle network variability and minimize 429 errors.
        self.__latency_window = deque(maxlen=100)
        self.__buffer = 40  # Initial buffer to avoid rate violations.
        self.__min_buffer = 30
        self.__max_buffer = 50

    # Adjusts the buffer based on observed latencies to adapt to network conditions.
    def update_buffer(self):
        if self.__latency_window:
            avg_latency = mean(self.__latency_window)
            self.__buffer = min(self.__max_buffer, max(self.__min_buffer, int(avg_latency * 1.1)))

    # Records request latency to update the buffer dynamically.
    def record_latency(self, latency: int):
        self.__latency_window.append(latency)
        self.update_buffer()

    # Ensures requests adhere to rate limits using circular buffer checks.
    @contextlib.asynccontextmanager
    async def acquire(self, timeout_ms: int = 0):
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

# Manages the main queue and DLQ to handle retries and expired requests efficiently.
class QueueManager:
    def __init__(self, main_queue: Queue, dlq_queue: Queue, logger: logging.Logger):
        self.main_queue = main_queue
        self.dlq_queue = dlq_queue
        self.logger = logger
        self.__max_retry_count = 5  # Limit retries to prevent infinite loops.
        self.__graveyard = set()  # Track requests that reached retry limit.

        # Store queue size history for monitoring and analysis.
        self.main_queue_size_history = deque(maxlen=100)
        self.dlq_size_history = deque(maxlen=100)

    async def get_from_main(self):
        return await self.main_queue.get()

    async def add_to_dlq(self, request: Request):
        await self.dlq_queue.put(request)

    # Requeues failed requests from the DLQ, or moves them to the graveyard after max retries.
    async def requeue_from_dlq(self, benchmark: Benchmark):
        while True:
            request: Request = await self.dlq_queue.get()
            if request.retry_count >= self.__max_retry_count:
                self.__graveyard.add(request.req_id)
                print(f"Request {request.req_id} moved to the graveyard.")
                benchmark.record_failure()
            else:
                request.retry_count += 1
                request.update_create_time()
                await self.main_queue.put(request)

            self.dlq_queue.task_done()

    # Periodically monitors queue sizes and logs warnings if thresholds are exceeded.
    async def monitor_queues(self, interval: int = 5):
        while True:
            main_queue_size = self.main_queue.qsize()
            dlq_size = self.dlq_queue.qsize()
            graveyard_size = len(self.__graveyard)

            self.main_queue_size_history.append(main_queue_size)
            self.dlq_size_history.append(dlq_size)

            avg_main_size = sum(self.main_queue_size_history) / len(self.main_queue_size_history)
            avg_dlq_size = sum(self.dlq_size_history) / len(self.dlq_size_history)

            self.logger.info(f"Queue Sizes - Main: {main_queue_size}, DLQ: {dlq_size}, Graveyard: {graveyard_size}")
            self.logger.info(f"Average Queue Sizes - Main: {avg_main_size:.2f}, DLQ: {avg_dlq_size:.2f}")

            if main_queue_size > 1000:
                print(f"Main queue size ({main_queue_size}) exceeds 1000!")
            if dlq_size > 100:
                print(f"DLQ size ({dlq_size}) exceeds 100!")

            await asyncio.sleep(interval)

# Worker processes requests while respecting rate limits and handling errors gracefully.
async def exchange_facing_worker(url: str, api_key: str, queue_manager: QueueManager, logger: logging.Logger, benchmark: Benchmark):
    """Processes requests from the main queue while adhering to rate limits."""
    async with aiohttp.ClientSession() as session:
        rate_limiter = RateLimiter(PER_SEC_RATE, DURATION_MS_BETWEEN_REQUESTS)

        while True:
            request: Request = await queue_manager.get_from_main()
            remaining_ttl = REQUEST_TTL_MS - (timestamp_ms() - request.create_time)

            # If the request has expired, send it to the DLQ.
            if remaining_ttl <= 0:
                await queue_manager.add_to_dlq(request)
                queue_manager.main_queue.task_done()
                continue

            try:
                # Acquire a rate-limited slot for the request.
                async with rate_limiter.acquire(timeout_ms=remaining_ttl):
                    async with async_timeout.timeout(1.0):
                        data = {'api_key': api_key, 'nonce': timestamp_ms(), 'req_id': request.req_id}
                        async with session.get(url, params=data) as resp:
                            if resp.status == 200:
                                # Record success with latency and log the response.
                                latency = timestamp_ms() - request.create_time
                                rate_limiter.record_latency(latency)
                                benchmark.record_success(latency)
                                logger.info(f"API response: {await resp.json()}")
                            else:
                                # On failure, move the request to the DLQ for retry.
                                logger.warning(f"Request {request.req_id} failed with status {resp.status}.")
                                await queue_manager.add_to_dlq(request)

            except (RateLimiterTimeout, asyncio.TimeoutError) as e:
                # Handle rate limiter timeouts or request timeouts by retrying via the DLQ.
                logger.error(f"Timeout for request {request.req_id}: {e}")
                await queue_manager.add_to_dlq(request)
            except aiohttp.ClientError as e:
                # Handle network issues by moving the request to the DLQ.
                logger.error(f"Network error for request {request.req_id}: {e}")
                await queue_manager.add_to_dlq(request)
            finally:
                # Mark the request as processed in the main queue.
                queue_manager.main_queue.task_done()

def main():
    """Entry point to start the request generation and processing system."""
    url = "http://127.0.0.1:9999/api/request"
    loop = asyncio.get_event_loop()

    main_queue = asyncio.Queue()  # Main queue for incoming requests.
    dlq_queue = asyncio.Queue()   # DLQ for requests that failed or expired.

    # Configure loggers for debugging and metrics tracking.
    logger = configure_logger("debug")
    stats = configure_logger("stats")

    # Create benchmark and queue manager instances.
    benchmark = Benchmark(stats)
    
    # Inject shared instance of main queue, DLQ, and logger into the queue manager.
    queue_manager = QueueManager(main_queue, dlq_queue, stats)

    # Launch tasks: request generation, DLQ management, workers, and metrics printing.
    loop.create_task(generate_requests(queue_manager.main_queue))
    loop.create_task(queue_manager.requeue_from_dlq(benchmark))

    # Spawn a worker for each API key to process requests concurrently.
    for api_key in VALID_API_KEYS:
        loop.create_task(exchange_facing_worker(url, api_key, queue_manager, logger, benchmark))

    # Launch periodic metrics printing and queue monitoring.
    loop.create_task(benchmark.metrics_printer())
    loop.create_task(queue_manager.monitor_queues())

    # Start the event loop to run the system indefinitely.
    loop.run_forever()

if __name__ == '__main__':
    main()
