import sys
import time
import random
import logging
import contextlib

import asyncio
from asyncio import Queue
import aiohttp
import async_timeout
from threading import Lock
from concurrent.futures import ThreadPoolExecutor
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
    MAX_SLEEP_MS = 1000 / PER_SEC_RATE / len(VALID_API_KEYS) * 1.05 * 2.0
    while True:
        queue.put_nowait(Request(curr_req_id))
        curr_req_id += 1
        sleep_ms = random.randint(0, MAX_SLEEP_MS)
        await asyncio.sleep(sleep_ms / 1000.0)


def timestamp_ms() -> int:
    return int(time.time() * 1000)

# endregion


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


class RateLimiterTimeout(Exception):
    pass




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


class Request:
    def __init__(self, req_id):
        self.req_id = req_id
        self.create_time = timestamp_ms()
        

class ThreadSafeRateLimiter:
    def __init__(self, per_second_rate, min_duration_ms_between_requests):
        self.per_second_rate = per_second_rate
        self.min_duration_ms_between_requests = min_duration_ms_between_requests
        self.request_times = [0] * per_second_rate
        self.curr_idx = 0
        self.lock = Lock()

    @contextlib.asynccontextmanager
    async def acquire(self, timeout_ms=0):
        enter_ms = timestamp_ms()
        while True:
            now = timestamp_ms()

            if now - enter_ms > timeout_ms > 0:
                raise RateLimiterTimeout()

            with self.lock:
                if now - self.request_times[self.curr_idx] > 1050:
                    self.request_times[self.curr_idx] = now
                    self.curr_idx = (self.curr_idx + 1) % self.per_second_rate
                    break

            await asyncio.sleep(0.001)
        yield self


async def exchange_facing_worker(url: str, api_key: str, queue: Queue, logger: logging.Logger, benchmark: Benchmark):
    async with aiohttp.ClientSession() as session:
        rate_limiter = ThreadSafeRateLimiter(PER_SEC_RATE, DURATION_MS_BETWEEN_REQUESTS)
        while True:
            request: Request = await queue.get()
            remaining_ttl = REQUEST_TTL_MS - (timestamp_ms() - request.create_time)
            if remaining_ttl <= 0:
                logger.warning(f"Ignoring request {request.req_id} from queue due to TTL")
                benchmark.record_failure()
                queue.task_done()
                continue

            try:
                async with rate_limiter.acquire(timeout_ms=remaining_ttl):
                    async with async_timeout.timeout(1.0):
                        data = {'api_key': api_key, 'nonce': timestamp_ms(), 'req_id': request.req_id}
                        async with session.get(url, params=data) as resp:
                            json = await resp.json()
                            latency = timestamp_ms() - request.create_time
                            if json['status'] == 'OK':
                                logger.info(f"API response: status {resp.status}, resp {json}")
                                benchmark.record_success(latency)
                            else:
                                logger.warning(f"API response: status {resp.status}, resp {json}")
                                benchmark.record_failure()
            except (RateLimiterTimeout, asyncio.TimeoutError):
                logger.warning(f"Ignoring request {request.req_id} due to timeout")
                benchmark.record_failure()
            finally:
                queue.task_done()


async def main():
    url = "http://127.0.0.1:9999/api/request"
    queue = asyncio.Queue()
    logger = configure_logger("debug")
    benchmark = Benchmark(configure_logger("stats"))

    executor = ThreadPoolExecutor()
    asyncio.create_task(generate_requests(queue))

    tasks = [
        exchange_facing_worker(url, api_key, queue, logger, benchmark)
        for api_key in VALID_API_KEYS
    ]

    metrics_task = asyncio.create_task(metrics_printer(benchmark))
    await asyncio.gather(*tasks, metrics_task)


if __name__ == '__main__':
    asyncio.run(main())
