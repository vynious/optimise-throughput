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
    '46V5EZ5K2DFAGW85J18L50SGO25WJ5JE'
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

# start benchmark region
def configure_logger(name=None):
    logger = logging.getLogger(name)
    if name == "debug":
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(formatter)
        logger.addHandler(sh)

        fh = logging.FileHandler("async-debug.log", mode="a")
        fh.setFormatter(formatter)
        logger.addHandler(fh)

        logger.setLevel(logging.DEBUG)
    elif name == "stats":
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(formatter)
        logger.addHandler(sh)

        fh = logging.FileHandler("status.log", mode="a")
        fh.setFormatter(formatter)
        logger.addHandler(fh)

        logger.setLevel(logging.DEBUG)
        
    return logger


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

    def record_success(self, latency_ms):
        self.total_successful_requests += 1
        self.total_latencies.append(latency_ms)
        self.interval_successful_requests += 1
        self.interval_latencies.append(latency_ms)

    def record_failure(self):
        self.total_failed_requests += 1
        self.interval_failed_requests += 1

    def print_metrics(self):
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


async def metrics_printer(benchmark: Benchmark, interval=5):
    while True:
        await asyncio.sleep(interval)
        benchmark.print_metrics()

        
# end of benchmark region

class Request:
    def __init__(self, req_id, retry_count=0):
        self.req_id = req_id
        self.create_time = timestamp_ms()
        self.retry_count = retry_count  # Initialize retry count

    def update_create_time(self):
        self.create_time = timestamp_ms()

class RateLimiterTimeout(Exception):
    pass


class RateLimiter:
    def __init__(self, per_second_rate, min_duration_ms_between_requests):
        self.__per_second_rate = per_second_rate
        self.__min_duration_ms_between_requests = min_duration_ms_between_requests
        self.__request_times = [0] * per_second_rate
        self.__curr_idx = 0

        self.__latency_window = deque(maxlen=100)  # record of the last 100 latencies
        self.__min_buffer = 30  # min buffer (ms)
        self.__buffer = 40  # initial buffer (ms)
        self.__max_buffer = 50  # max buffer (ms)

    def update_buffer(self):
        # calculate a moving average of the recent latencies
        if len(self.__latency_window) > 0:
            avg_latency = sum(self.__latency_window) / len(self.__latency_window)
            # adjust buffer based on average latency
            self.__buffer = min(self.__max_buffer, max(self.__min_buffer, int(avg_latency * 1.1)))

    def record_latency(self, latency):
        self.__latency_window.append(latency)
        self.update_buffer()
    
    @contextlib.asynccontextmanager
    async def acquire(self, timeout_ms=0):
        enter_ms = timestamp_ms()
        buffer = self.__buffer
        initial_buffer = self.__min_duration_ms_between_requests * self.__per_second_rate
        
        while True:
            now = timestamp_ms()

            if now - enter_ms > timeout_ms > 0:
                raise RateLimiterTimeout()

            if now - self.__request_times[self.__curr_idx] <= initial_buffer + buffer:
                # sleep the exact remaining time to the next second
                sleep_time = (initial_buffer + buffer - (now - self.__request_times[self.__curr_idx])) / 1000
                await asyncio.sleep(sleep_time)
                continue
            
            break
        
        self.__request_times[self.__curr_idx] = now
        self.__curr_idx = (self.__curr_idx + 1) % self.__per_second_rate
        yield self


class QueueManager:
    """
    Manages the main queue and DLQ with priority handling.
    """
    
    def __init__(self, main_queue: asyncio.Queue, dlq_queue: asyncio.Queue):
        self.main_queue = main_queue  # main queue for normal requests
        self.dlq_queue = dlq_queue    # DLQ for failed requests
        self.__max_retry_count = 5  # max retry count for requests can be further adjusted
        self.__graveyard = set(); # set to store dropped requests

    async def add_request(self, request: Request):
        """Adds a request to the main queue."""
        await self.main_queue.put(request)

    async def requeue_from_dlq(self, benchmark: Benchmark):
        """
        Prioritize DLQ requests by re-queuing them into the main queue.
        
        Prioritisation Logic: Because the generate_requests() has a delay between each generated requests, allowing DLQ requests to be re-queued as soon as possible.
        """
        max_retry = self.__max_retry_count
        while True:
            request: Request = await self.dlq_queue.get()  # get request from dlq
    
            if request.retry_count >= max_retry:
                print(f"Request {request.req_id} has reached max retry count. sending to the graveyard.")
                self.__graveyard.add(request.req_id) # store the 💀 requests
                print(f"Graveyard count: {len(self.__graveyard)}")
                benchmark.record_failure()

            else:
                request.retry_count += 1 # increment retry count
                request.update_create_time()  # refresh timestamp to avoid TTL issues
                await self.main_queue.put(request)  # add back to the main queue
            
            self.dlq_queue.task_done()

    async def add_to_dlq(self, request: Request):
        """
        Adds failed requests to the DLQ.
        """
        await self.dlq_queue.put(request)
    
    async def get_from_main(self):
        """
        Get a request from the main queue.
        """
        return await self.main_queue.get()


async def exchange_facing_worker(url: str, api_key: str, queue_manager: QueueManager, logger: logging.Logger, benchmark: Benchmark):
    async with aiohttp.ClientSession() as session:
        rate_limiter = RateLimiter(PER_SEC_RATE, DURATION_MS_BETWEEN_REQUESTS)
        while True:
            request: Request = await queue_manager.get_from_main()
            remaining_ttl = REQUEST_TTL_MS - (timestamp_ms() - request.create_time)

            if remaining_ttl <= 0:
                await queue_manager.add_to_dlq(request)  # move to dlq for re-processing
                queue_manager.main_queue.task_done()
                continue

            try:
                async with rate_limiter.acquire(timeout_ms=remaining_ttl):
                    async with async_timeout.timeout(1.0):
                        data = {'api_key': api_key, 'nonce': timestamp_ms(), 'req_id': request.req_id}
                        async with session.get(url, params=data) as resp:
                            latency = timestamp_ms() - request.create_time
                            rate_limiter.record_latency(latency)
                            json = await resp.json()
                            if json['status'] == 'OK':
                                logger.info(f"API response: status {resp.status}, resp {json}")
                                benchmark.record_success(latency)
                            else:
                                logger.warning(f"API response: status {resp.status}, resp {json}")
            except (RateLimiterTimeout, asyncio.TimeoutError):
                logger.warning(f"Timeout for request {request.req_id}")
                await queue_manager.add_to_dlq(request)  # retry via DLQ
            finally:
                queue_manager.main_queue.task_done()
                
                
def main():
    url = "http://127.0.0.1:9999/api/request"
    loop = asyncio.get_event_loop()

    main_queue = asyncio.Queue()  # main queue
    dlq_queue = asyncio.Queue()   # DLQ for failed requests
    
    queue_manager = QueueManager(main_queue, dlq_queue)  # manage both queues

    logger = configure_logger("debug")
    benchmark = Benchmark(configure_logger("stats"))

    # async task to generate requests
    loop.create_task(generate_requests(queue_manager.main_queue))

    # async task to requeue failed requests from DLQ
    loop.create_task(queue_manager.requeue_from_dlq(benchmark))

    # ayync task for each API key to run exchange_facing_worker
    for api_key in VALID_API_KEYS:
        loop.create_task(exchange_facing_worker(url, api_key, queue_manager, logger, benchmark))

    # async task to print metrics 
    loop.create_task(metrics_printer(benchmark))

    # run the event loop
    loop.run_forever()

if __name__ == '__main__':
    main()
