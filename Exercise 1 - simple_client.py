import sys
import time
import random
import logging
import contextlib

import asyncio
from asyncio import Queue
import aiohttp
import async_timeout
from statistics import mean



# region: DO NOT CHANGE - the code within this region can be assumed to be "correct"

PER_SEC_RATE = 20
DURATION_MS_BETWEEN_REQUESTS = int(1000 / PER_SEC_RATE)
REQUEST_TTL_MS = 1000
VALID_API_KEYS = ['UT4NHL1J796WCHULA1750MXYF9F5JYA6',
                  '8TY2F3KIL38T741G1UCBMCAQ75XU9F5O',
                  '954IXKJN28CBDKHSKHURQIVLQHZIEEM9',
                  'EUU46ID478HOO7GOXFASKPOZ9P91XGYS',
                  '46V5EZ5K2DFAGW85J18L50SGO25WJ5JE']

async def generate_requests(queue: Queue):
    """
    co-routine responsible for generating requests

    :param queue:
    :param logger:
    :return:
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
        # only add handlers to root logger
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(formatter)
        logger.addHandler(sh)

        fh = logging.FileHandler(f"async-debug.log", mode="a")
        fh.setFormatter(formatter)
        logger.addHandler(fh)

        logger.setLevel(logging.DEBUG)
    if name == "stats":
        # only add handlers to root logger
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(formatter)
        logger.addHandler(sh)

        fh = logging.FileHandler(f"status.log", mode="a")
        fh.setFormatter(formatter)
        logger.addHandler(fh)

        logger.setLevel(logging.DEBUG)
        
    return logger



class Benchmark:
    def __init__(self, logger: logging.Logger):
        self.start_time = timestamp_ms()

        # Accumulated metrics
        self.total_successful_requests = 0
        self.total_failed_requests = 0
        self.total_latencies = []

        # Interval metrics (reset every interval)
        self.interval_successful_requests = 0
        self.interval_failed_requests = 0
        self.interval_latencies = []

        self.logger = logger  # Use the provided logger

    def record_success(self, latency_ms):
        # Accumulate overall metrics
        self.total_successful_requests += 1
        self.total_latencies.append(latency_ms)

        # Track metrics for this interval
        self.interval_successful_requests += 1
        self.interval_latencies.append(latency_ms)

    def record_failure(self):
        # Accumulate overall metrics
        self.total_failed_requests += 1

        # Track metrics for this interval
        self.interval_failed_requests += 1

    def print_metrics(self):
        # Calculate elapsed time for accumulated metrics
        elapsed = (timestamp_ms() - self.start_time) / 1000  # in seconds

        # Calculate averages and throughput for accumulated data
        avg_latency = mean(self.total_latencies) if self.total_latencies else 0
        total_throughput = self.total_successful_requests / elapsed if elapsed > 0 else 0

        # Calculate averages and throughput for the current interval
        interval_avg_latency = mean(self.interval_latencies) if self.interval_latencies else 0
        interval_throughput = (
            self.interval_successful_requests / 5  # Assuming 5-second interval
            if self.interval_successful_requests > 0
            else 0
        )

        # Log accumulated metrics
        self.logger.info("\n--------------------------------------")
        self.logger.info("\n--- Accumulated Benchmark Metrics ---")
        self.logger.info(f"Elapsed Time: {elapsed:.2f} seconds")
        self.logger.info(f"Total Successful Requests: {self.total_successful_requests}")
        self.logger.info(f"Total Failed Requests: {self.total_failed_requests}")
        self.logger.info(f"Total Throughput: {total_throughput:.2f} req/sec")
        self.logger.info(f"Average Latency (Total): {avg_latency:.2f} ms")

        # Log interval metrics
        # self.logger.info("\n--- Interval Benchmark Metrics (Last 5 sec) ---")
        # self.logger.info(f"Successful Requests: {self.interval_successful_requests}")
        # self.logger.info(f"Failed Requests: {self.interval_failed_requests}")
        # self.logger.info(f"Throughput: {interval_throughput:.2f} req/sec")
        # self.logger.info(f"Average Latency: {interval_avg_latency:.2f} ms")

        # Reset interval metrics for the next interval
        self.interval_successful_requests = 0
        self.interval_failed_requests = 0
        self.interval_latencies = []


async def metrics_printer(benchmark: Benchmark, interval=5):
    """Periodically print metrics."""
    while True:
        await asyncio.sleep(interval)
        benchmark.print_metrics()


class Request:
    def __init__(self, req_id):
        self.req_id = req_id
        self.create_time = timestamp_ms()

class RateLimiterTimeout(Exception):
    pass

class RateLimiter:
    def __init__(self, per_second_rate, min_duration_ms_between_requests):
        self.__per_second_rate = per_second_rate
        self.__min_duration_ms_between_requests = min_duration_ms_between_requests
        self.__last_request_time = 0
        self.__request_times = [0] * per_second_rate
        self.__curr_idx = 0

    @contextlib.asynccontextmanager
    async def acquire(self, timeout_ms=0):
        enter_ms = timestamp_ms()
        while True:
            now = timestamp_ms()
            
            # request can timeout because of rate limiting checks below
            # 1. if the current time and the last request time are too close, sleep for a bit
            # 2. if the difference between the current time and the last 20th request is less than 1 second
            # timeout 
            # print(f'now: {now}, enter_ms: {enter_ms}, timeout_ms: {timeout_ms}')
            if now - enter_ms > timeout_ms > 0:
                raise RateLimiterTimeout()

            # if the current time and the last request time are too close, sleep for a bit
            # sleeps and try again with a new 'now' timestamp
            # !! this is redundant because the circular buffer is checking for the same condition. 
            # if now - self.__last_request_time <= self.__min_duration_ms_between_requests:
            #     # await asyncio.sleep(0.0001)
            #     sleep_duration = min(0.001, (self.__min_duration_ms_between_requests - (now - self.__last_request_time)) / 1000)
            #     await asyncio.sleep(sleep_duration)
            #     continue
            
            # current time and last request time should have a 1 second difference 
            # changed from <= to < because there will be additional latency pushing it to at least 1 second
            if now - self.__request_times[self.__curr_idx] <= 1050: 
                # print(f'sleeping the difference in request time is {now - self.__request_times[self.__curr_idx]}')
                # await asyncio.sleep(0.0001)
                await asyncio.sleep(0.001)
                continue

            break

        # update of the last request time and the new current index
        # !! no need to store the __last_request_time because it is the same as the current index as we are not using it for comparsion since we are using a circular buffer
        self.__last_request_time = self.__request_times[self.__curr_idx] = now
        self.__curr_idx = (self.__curr_idx + 1) % self.__per_second_rate
        yield self


async def exchange_facing_worker(url: str, api_key: str, queue: Queue, logger: logging.Logger, benchmark: Benchmark):
    async with aiohttp.ClientSession() as session:
        rate_limiter = RateLimiter(PER_SEC_RATE, DURATION_MS_BETWEEN_REQUESTS)
        while True:
            request: Request = await queue.get()
            remaining_ttl = REQUEST_TTL_MS - (timestamp_ms() - request.create_time)
            if remaining_ttl <= 0:
                logger.warning(f"fuck ignoring request {request.req_id} from queue due to TTL")
                benchmark.record_failure()
                queue.task_done()
                continue
            try:
                async with rate_limiter.acquire(timeout_ms=remaining_ttl):
                    async with async_timeout.timeout(1.0):
                        data = {'api_key': api_key, 'nonce': timestamp_ms(), 'req_id': request.req_id}
                        async with session.request('GET', url, data=data) as resp:
                            json = await resp.json()
                            latency = timestamp_ms() - request.create_time
                            if json['status'] == 'OK':
                                logger.info(f"API response: status {resp.status}, resp {json}")
                                benchmark.record_success(latency)
                            else:
                                logger.warning(f"API response: status {resp.status}, resp {json}")
                            benchmark.record_failure()
            except (RateLimiterTimeout, asyncio.TimeoutError):
                logger.warning(f"fuck ignoring request {request.req_id} due to timeout")
                benchmark.record_failure()
            finally:
                queue.task_done()
            

def main():
    url = "http://127.0.0.1:9999/api/request"
    loop = asyncio.get_event_loop()
    queue = asyncio.Queue()
    logger = configure_logger("debug")
    benchmark = Benchmark(configure_logger("stats"))


    # Start request generation
    loop.create_task(generate_requests(queue=queue))

    # Start workers
    for api_key in VALID_API_KEYS:
        loop.create_task(exchange_facing_worker(url, api_key, queue, logger, benchmark))

    # Start metrics printer
    loop.create_task(metrics_printer(benchmark))

    loop.run_forever()


if __name__ == '__main__':
    main()

