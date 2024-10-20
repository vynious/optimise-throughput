import time
import asyncio
import logging
from collections import deque

from asyncio import Queue
import benchmark

def timestamp_ms() -> int:
    return int(time.time() * 1000)


class Request:
    """Represents a request with retry capabilities."""
    def __init__(self, req_id: int, retry_count: int = 0):
        self.req_id = req_id
        self.retry_count = retry_count
        self.create_time = timestamp_ms()

    def update_create_time(self):
        """Refresh the request's timestamp."""
        self.create_time = timestamp_ms()


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

    async def requeue_from_dlq(self, benchmark: benchmark.Benchmark):
        """Retry requests from the DLQ."""
        while True:
            request: Request = await self.dlq_queue.get()
            if request.retry_count >= self.__max_retry_count:
                self.__graveyard.add(request.req_id)
                self.logger.info(f"Request {request.req_id} moved to the graveyard.")
                benchmark.record_failure()
            else:
                request.retry_count += 1
                request.update_create_time()
                await self.main_queue.put(request)

            self.dlq_queue.task_done()

    async def monitor_queues(self, interval: int = 5):
        """Monitor queue sizes and log statistics periodically."""
        while True:
            # Get queue sizes
            main_queue_size = self.main_queue.qsize()
            dlq_size = self.dlq_queue.qsize()
            graveyard_size = len(self.__graveyard)

            # Track history for averages
            self.main_queue_size_history.append(main_queue_size)
            self.dlq_size_history.append(dlq_size)

            # Calculate averages
            avg_main_size = sum(self.main_queue_size_history) / len(self.main_queue_size_history)
            avg_dlq_size = sum(self.dlq_size_history) / len(self.dlq_size_history)

            # Log queue sizes and averages
            self.logger.info(
                f"Queue Sizes - Main: {main_queue_size}, DLQ: {dlq_size}, Graveyard: {graveyard_size}"
            )
            self.logger.info(
                f"Average Queue Sizes - Main: {avg_main_size:.2f}, DLQ: {avg_dlq_size:.2f}"
            )

            # Check thresholds and log warnings if exceeded
            if main_queue_size > 1000:
                self.logger.warning(f"Main queue size ({main_queue_size}) exceeds 1000!")
            if dlq_size > 100:
                self.logger.warning(f"DLQ size ({dlq_size}) exceeds 100!")

            # Sleep for the specified interval (non-blocking)
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