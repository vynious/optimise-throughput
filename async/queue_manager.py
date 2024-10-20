import time

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
    def __init__(self, main_queue: Queue, dlq_queue: Queue):
        self.main_queue = main_queue
        self.dlq_queue = dlq_queue
        self.__max_retry_count = 5
        self.__graveyard = set()

    async def add_request(self, request: Request):
        """Add a new request to the main queue."""
        await self.main_queue.put(request)

    async def requeue_from_dlq(self, benchmark: benchmark.Benchmark):
        """Retry requests from the DLQ."""
        while True:
            request: Request = await self.dlq_queue.get()
            if request.retry_count >= self.__max_retry_count:
                self.__graveyard.add(request.req_id)
                benchmark.record_failure()
            else:
                request.retry_count += 1
                request.update_create_time()
                await self.main_queue.put(request)

            self.dlq_queue.task_done()

    async def add_to_dlq(self, request: Request):
        """Move failed requests to the DLQ."""
        await self.dlq_queue.put(request)
