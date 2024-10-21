import sys
import time
import random

import threading
from queue import Queue
import requests
import asyncio
from request import Request
from queue_manager import QueueManager
from rate_limiter import ThreadSafeRateLimiter, RateLimiterTimeout
from benchmark import Benchmark
from logger import configure_logger
from memory_profiler import profile

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

class ThreadSafeQueue(Queue):
    pass  # thread safety from queue.Queue

# same as non-threadsafe version but without asyncio.sleep
def generate_requests_threadsafe(queue: ThreadSafeQueue):
    curr_req_id = 0
    MAX_SLEEP_MS = int(1000 / PER_SEC_RATE / len(VALID_API_KEYS) * 1.05 * 2.0)
    while True:
        queue.put_nowait(Request(curr_req_id))
        curr_req_id += 1
        sleep_ms = random.randint(0, MAX_SLEEP_MS)
        time.sleep(sleep_ms / 1000.0)

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

def exchange_facing_worker(api_key, queue_manager: QueueManager, rate_limiter: ThreadSafeRateLimiter, logger, benchmark: Benchmark):
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

@profile
def main():
    logger = configure_logger("debug")
    benchmark_logger = configure_logger("stats")
    
    main_queue = ThreadSafeQueue()
    dlq_queue = ThreadSafeQueue()
    
    queue_manager = QueueManager(main_queue=main_queue, dlq_queue=dlq_queue, logger=benchmark_logger)
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
        
    # exchange-facing workers in threads
    threads = []
    for api_key in VALID_API_KEYS:
        t = threading.Thread(
            target=exchange_facing_worker,
            args=(api_key, queue_manager, rate_limiters[api_key], logger, benchmark),
            daemon=True
        )
        t.start()
        threads.append(t)

    # DLQ requeue task in a thread
    dlq_requeue_thread = threading.Thread(
        target=queue_manager.requeue_from_dlq,
        args=(benchmark,),
        daemon=True
    )
    dlq_requeue_thread.start()

    # metrics printer in a thread
    metrics_thread = threading.Thread(
        target=benchmark.metrics_printer,
        daemon=True
    )
    metrics_thread.start()
    
    # Start the queue monitoring thread
    queue_monitor_thread = threading.Thread(
        target=queue_manager.monitor_queues,
        daemon=True
    )
    queue_monitor_thread.start()

    # Keep the main thread running
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass

if __name__ == '__main__':
    main()