from queue import Queue
from collections import deque
import logging
import threading
from request import Request
from benchmark import Benchmark
import time




class QueueManager:
    def __init__(self, main_queue: Queue, dlq_queue: Queue, logger: logging.Logger):
        self.main_queue = main_queue
        self.dlq_queue = dlq_queue
        self.logger = logger
        self.max_retry_count = 5
        self.graveyard = set()
        self.lock = threading.Lock()
        
        # Monitoring attributes
        self.main_queue_size_history = deque(maxlen=100)
        self.dlq_size_history = deque(maxlen=100)

    def add_request(self, request: Request):
        """Add a new request to the main queue."""
        with self.lock:
            self.main_queue.put(request)

    def requeue_from_dlq(self, benchmark: Benchmark):
        """Requeue requests from DLQ to the main queue with priority."""
        while True:
            request: Request = self.dlq_queue.get()
            if request.retry_count >= self.max_retry_count:
                with self.lock:
                    self.graveyard.add(request.req_id)
                self.logger.info(f"Request {request.req_id} moved to graveyard.")
                benchmark.record_failure()
            else:
                request.retry_count += 1
                request.update_create_time()
                with self.lock:
                    self.main_queue.put(request)
            
            self.dlq_queue.task_done()

    def add_to_dlq(self, request: Request):
        """Add a failed request to the DLQ."""
        with self.lock:
            self.dlq_queue.put(request)
    
    def get_from_main(self):
        """Get a request from the main queue."""
        return self.main_queue.get()

    def monitor_queues(self, interval=5):
        """Monitor queue sizes and log statistics."""
        while True:
            with self.lock:
                main_queue_size = self.main_queue.qsize()
                dlq_size = self.dlq_queue.qsize()
                graveyard_size = len(self.graveyard)
                
                self.main_queue_size_history.append(main_queue_size)
                self.dlq_size_history.append(dlq_size)
            
            self.logger.info(f"Queue Sizes - Main: {main_queue_size}, DLQ: {dlq_size}, Graveyard: {graveyard_size}")
            
            avg_main_size = sum(self.main_queue_size_history) / len(self.main_queue_size_history)
            avg_dlq_size = sum(self.dlq_size_history) / len(self.dlq_size_history)
            
            self.logger.info(f"Average Queue Sizes - Main: {avg_main_size:.2f}, DLQ: {avg_dlq_size:.2f}")
            
            if main_queue_size > 1000:
                self.logger.warning(f"Main queue size ({main_queue_size}) exceeds 1000!")
            if dlq_size > 100:
                self.logger.warning(f"DLQ size ({dlq_size}) exceeds 100!")
            
            time.sleep(interval)

    def get_queue_stats(self):
        """Get current queue statistics."""
        with self.lock:
            return {
                'main_queue_size': self.main_queue.qsize(),
                'dlq_size': self.dlq_queue.qsize(),
                'avg_main_queue_size': sum(self.main_queue_size_history) / len(self.main_queue_size_history) if self.main_queue_size_history else 0,
                'avg_dlq_size': sum(self.dlq_size_history) / len(self.dlq_size_history) if self.dlq_size_history else 0,
                'graveyard_size': len(self.graveyard)
            }