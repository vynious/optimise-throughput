import time
import asyncio
import logging
from collections import deque

from asyncio import Queue
import benchmark
from request import Request

def timestamp_ms() -> int:
    return int(time.time() * 1000)




class QueueManager:
    """Manages the main queue and the Dead Letter Queue (DLQ)."""
    def __init__(self, main_queue: Queue, dlq_queue: Queue, logger: logging.Logger):
        self.main_queue = main_queue
        self.dlq_queue = dlq_queue
        self.logger = logger
        self.__max_retry_count = 5
        self.__graveyard = set()

        self.main_queue_size_history = deque(maxlen=100)
        self.dlq_size_history = deque(maxlen=100)
        
    async def get_from_main(self):
        """Get a request from the main queue."""
        return await self.main_queue.get()
    
    async def add_to_dlq(self, request: Request):
        """Add a failed request to the DLQ."""
        return await self.dlq_queue.put(request)

    async def requeue_from_dlq(self, benchmark: benchmark.Benchmark):
        """Retry requests from the DLQ."""
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

    async def monitor_queues(self, interval: int = 5):
        """Monitor queue sizes and log statistics periodically."""
        while True:
            # get queue sizes
            main_queue_size = self.main_queue.qsize()
            dlq_size = self.dlq_queue.qsize()
            graveyard_size = len(self.__graveyard)

            # track history for averages
            self.main_queue_size_history.append(main_queue_size)
            self.dlq_size_history.append(dlq_size)

            # calculate averages
            avg_main_size = sum(self.main_queue_size_history) / len(self.main_queue_size_history)
            avg_dlq_size = sum(self.dlq_size_history) / len(self.dlq_size_history)

            # log queue sizes and averages
            self.logger.info(
                f"Queue Sizes - Main: {main_queue_size}, DLQ: {dlq_size}, Graveyard: {graveyard_size}"
            )
            self.logger.info(
                f"Average Queue Sizes - Main: {avg_main_size:.2f}, DLQ: {avg_dlq_size:.2f}"
            )

            # check thresholds and log warnings if exceeded
            if main_queue_size > 1000:
                print(f"Main queue size ({main_queue_size}) exceeds 1000!")
            if dlq_size > 100:
                print(f"DLQ size ({dlq_size}) exceeds 100!")

            # sleep for the specified interval (non-blocking)
            await asyncio.sleep(interval)

    def get_queue_stats(self):
        """Return current statistics about the queues."""
        return {
            'main_queue_size': self.main_queue.qsize(),
            'dlq_size': self.dlq_queue.qsize(),
            'avg_main_queue_size': sum(self.main_queue_size_history) / len(self.main_queue_size_history) if self.main_queue_size_history else 0,
            'avg_dlq_size': sum(self.dlq_size_history) / len(self.dlq_size_history) if self.dlq_size_history else 0,
            'graveyard_size': len(self.__graveyard)
        }