import time

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