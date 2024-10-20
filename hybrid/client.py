import time
import threading
import asyncio
from queue_manager import QueueManager
from request import Request
from benchmark import Benchmark
from logger import configure_logger
import aiohttp
import async_timeout
from queue import Queue
import random
import logging
from rate_limiter import RateLimiter, RateLimiterTimeout



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
                await queue_manager.add_to_dlq(request)
            except aiohttp.ClientError as e:
                await queue_manager.add_to_dlq(request)
            finally:
                queue_manager.main_queue.task_done()
                

                
def start_event_loop(api_keys, queue_manager: QueueManager, logger, benchmark):
    
    """Starts an event loop in a thread with assigned API keys."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    url = "http://127.0.0.1:9999/api/request"

    # Create task to generate requests
    loop.create_task(generate_requests(queue_manager.main_queue))

    # Create exchange-facing workers for each API key
    for api_key in api_keys:
        loop.create_task(exchange_facing_worker(url, api_key, queue_manager, logger, benchmark))

    # Create requeue from DLQ and metrics printer tasks
    loop.create_task(queue_manager.requeue_from_dlq(benchmark))
    loop.create_task(benchmark.metrics_printer())
    loop.create_task(queue_manager.monitor_queues())

    loop.run_forever()

def main():
    # Configure loggers and benchmarking

    stats_logger = configure_logger("stats")
    benchmark = Benchmark(stats_logger)

    # Split API keys into two groups
    api_keys_group1 = VALID_API_KEYS[:3]  # 3 API keys
    api_keys_group2 = VALID_API_KEYS[3:]  # 2 API keys

    # Create QueueManagers for each thread
    queue_manager1 = QueueManager(asyncio.Queue(), asyncio.Queue(), stats_logger)
    queue_manager2 = QueueManager(asyncio.Queue(), asyncio.Queue(), stats_logger)

    # Start two threads, each with its own event loop
    thread1 = threading.Thread(
        target=start_event_loop, 
        args=(api_keys_group1, queue_manager1, stats_logger, benchmark),
        daemon=True
    )
    thread2 = threading.Thread(
        target=start_event_loop, 
        args=(api_keys_group2, queue_manager2, stats_logger, benchmark),
        daemon=True
    )

    # Start both threads
    thread1.start()
    thread2.start()

    # Keep the main thread running
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass

if __name__ == '__main__':
    main()
