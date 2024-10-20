import time
import random
import logging
import asyncio
import aiohttp
import async_timeout

from asyncio import Queue
from rate_limiter import RateLimiter, RateLimiterTimeout
from queue_manager import QueueManager
from benchmark import Benchmark
from request import Request
from logger import configure_logger

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



async def exchange_facing_worker(
    url: str, api_key: str, queue_manager: QueueManager, 
    logger: logging.Logger, benchmark: Benchmark
):
    """Processes requests from the main queue."""
    async with aiohttp.ClientSession() as session:
        rate_limiter = RateLimiter(PER_SEC_RATE, DURATION_MS_BETWEEN_REQUESTS)

        while True:
            request: Request = await queue_manager.get_from_main()
            remaining_ttl = REQUEST_TTL_MS - (timestamp_ms() - request.create_time)

            if remaining_ttl <= 0:
                logger.warning(f"Request {request.req_id} expired. Moving to DLQ.")
                await queue_manager.add_to_dlq(request)
                queue_manager.main_queue.task_done()
                continue

            try:
                async with rate_limiter.acquire(timeout_ms=remaining_ttl):
                    async with async_timeout.timeout(1.0):
                        data = {'api_key': api_key, 'nonce': timestamp_ms(), 'req_id': request.req_id}
                        async with session.get(url, params=data) as resp:
                            if resp.status == 200:
                                latency = timestamp_ms() - request.create_time
                                rate_limiter.record_latency(latency)
                                benchmark.record_success(latency)
                                logger.info(f"API response: {await resp.json()}")
                            else:
                                logger.warning(f"Request {request.req_id} failed with status {resp.status}.")
                                await queue_manager.add_to_dlq(request)
            except (RateLimiterTimeout, asyncio.TimeoutError) as e:
                logger.warning(f"Timeout for request {request.req_id}: {str(e)}")
                await queue_manager.add_to_dlq(request)
            except aiohttp.ClientError as e:
                logger.error(f"Network error for request {request.req_id}: {str(e)}")
                await queue_manager.add_to_dlq(request)
            finally:
                queue_manager.main_queue.task_done()
                
def main():
    url = "http://127.0.0.1:9999/api/request"
    loop = asyncio.get_event_loop()

    main_queue = asyncio.Queue()  # main queue
    dlq_queue = asyncio.Queue()   # DLQ for failed requests
    

    logger = configure_logger("debug")
    stats = configure_logger("stats")
    
    benchmark = Benchmark(stats)
    
    queue_manager = QueueManager(main_queue, dlq_queue, stats)  # manage both queues


    # async task to generate requests
    loop.create_task(generate_requests(queue_manager.main_queue))

    # async task to requeue failed requests from DLQ
    loop.create_task(queue_manager.requeue_from_dlq(benchmark))

    # ayync task for each API key to run exchange_facing_worker
    for api_key in VALID_API_KEYS:
        loop.create_task(exchange_facing_worker(url, api_key, queue_manager, logger, benchmark))

    # async task to print metrics 
    loop.create_task(benchmark.metrics_printer())
    loop.create_task(queue_manager.monitor_queues())

    # run the event loop
    loop.run_forever()

if __name__ == '__main__':
    main()
