## Table of Contents

- [Benchmarking](#benchmarking)
  - [How Benchmarking is Performed](#how-benchmarking-is-performed)
- [Client Rate Limiter](#client-rate-limiter)
  - [Current Implementation](#current-implementation)
  - [Issue](#issue)
  - [Solution](#solution)
  - [Observation: Performance Improvement](#observation-performance-improvement)
  - [Caveat: Potential 429 Errors Due to Latency Discrepancies](#caveat-potential-429-errors-due-to-latency-discrepancies)
  - [Improved Version](#improved-version)
  - [Conclusion](#conclusion)
- [Queue Management](#queue-management)
  - [Current Implementation](#current-implementation-1)
  - [Issue](#issue-1)
  - [Solution: Queue Manager with Dead Letter Queue (DLQ)](#solution-queue-manager-with-dead-letter-queue-dlq)
    - [How Retry Requests Are Prioritized](#how-retry-requests-are-prioritized)
  - [Lifecycle](#lifecycle)
  - [Improved Version](#improved-version-1)
  - [Caveat: Slow Rate of Consuming Requests](#caveat-slow-rate-of-consuming-requests)
  - [Workarounds](#workarounds)

---

## **Benchmarking**

### **How Benchmarking is Performed**

Benchmarking aims to measure the performance of the rate limiter and the efficiency of request processing. It focuses on capturing metrics such as **throughput (TPS)**, **latency**, and **failure rates** to evaluate improvements accurately.

**Benchmarking Steps:**

1. **Setup:**
   - Utilize **5 API keys**, each associated with a worker task sending requests concurrently.
   - Implement a **rate limiter** to control the request rate per API key.
   - Initialize a **Benchmarking class** to record key metrics:
     - **Total successful requests**
     - **Total failed requests**
     - **Average latency per request**
     - **Throughput (requests per second, TPS)**

2. **Request Generation:**
   - Continuously generate requests and add them to a shared queue.

3. **Request Processing:**
   - Worker tasks fetch requests from the queue.
   - Each request passes through the **rate limiter** before being sent to the server.

4. **Metrics Measurement:**
   - The **Benchmark class** tracks:
     - Start and completion times of each request.
     - Success or failure status.
     - Latency of each request.

5. **Results Logging:**
   - Benchmark metrics are printed every 5 seconds, providing real-time performance feedback.

---

## **Client Rate Limiter**

### **Current Implementation**

The client-side rate limiter ensures that requests are sent to the server at a controlled rate, complying with the server's rate limiting policies.

```python
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
            if now - enter_ms > timeout_ms > 0:
                raise RateLimiterTimeout()

            # Fixed Interval Check
            if now - self.__last_request_time <= self.__min_duration_ms_between_requests:
                await asyncio.sleep(0.001)
                continue

            # Circular Buffer Check
            if now - self.__request_times[self.__curr_idx] <= 1000:
                await asyncio.sleep(0.001)
                continue

            break
        
        self.__last_request_time = self.__request_times[self.__curr_idx] = now
        self.__curr_idx = (self.__curr_idx + 1) % self.__per_second_rate
        yield self
```

---

### **Issue**

The current rate limiter implementation includes **two conditional checks** that both aim to regulate the rate of outgoing requests:

1. **Fixed Interval Check:**
   - Ensures that the time interval between consecutive requests does not fall below a predefined minimum (`min_duration_ms_between_requests`).
   - ```python
     if now - self.__last_request_time <= self.__min_duration_ms_between_requests:
         await asyncio.sleep(0.001)
         continue
     ```

2. **Circular Buffer Check:**
   - Ensures that the time difference between the current request and the request sent `per_second_rate` positions earlier is at least **1 second**.
   - ```python
     if now - self.__request_times[self.__curr_idx] <= 1000:
         await asyncio.sleep(0.001)
         continue
     ```

**Redundancy and Impact:**

- Both checks serve to regulate the request rate, resulting in overlapping functionality.
- The **Fixed Interval Check** adds unnecessary rigidity, limiting flexibility for bursty traffic.
- The additional context switching due to frequent `await asyncio.sleep(0.001)` calls introduces overhead and reduces throughput.

---

### **Solution**

To eliminate redundancy and improve efficiency, we remove the **Fixed Interval Check**. The **Circular Buffer Check** alone suffices for rate limiting, effectively handling both **bursty** and **constant-rate traffic**.

**Benefits:**

- **Adaptive Rate Limiting:**
  - Allows the client to send requests in bursts when needed, as long as the overall rate limit is respected.
- **Reduced Overhead:**
  - Eliminates unnecessary context switching caused by frequent sleep calls.
- **Improved Flexibility:**
  - Accommodates scenarios where varying traffic patterns are essential.

---

### **Observation: Performance Improvement**

**Benchmark Results:**

- **Before Removal:**
  - Throughput: ~74 TPS (Transactions Per Second)
- **After Removal:**
  - Throughput: ~85 TPS

**Reasoning:**

- **Reduced Context Switching:**
  - Eliminating the Fixed Interval Check reduces the number of times the coroutine yields control, decreasing overhead.
- **Efficient Rate Limiting:**
  - The Circular Buffer Check maintains compliance with rate limits without imposing unnecessary delays.
- **Better Resource Utilization:**
  - Allows the system to process more requests within the same time frame.

---

### **Caveat: Potential 429 Errors Due to Latency Discrepancies**

While removing the Fixed Interval Check improves throughput, it may lead to **HTTP 429 (Too Many Requests) errors** due to **timestamp discrepancies** between the client and server caused by network latency.

**How the Issue Occurs:**

- **Client-Side Rate Limiting:**
  - The client ensures that at least 1000 ms have passed between the current request and the one sent `per_second_rate` requests ago.
- **Server-Side Rate Limiting:**
  - The server measures the time between receiving requests.
- **Latency Variability:**
  - Network latency can vary between requests, causing the server's perceived time intervals to be shorter than the client's.

**Example Scenario:**

1. **First Request:**
   - Client sends at **0 ms**.
   - Network latency: **40 ms**.
   - Server receives at **40 ms**.

2. **20th Request:**
   - Client sends at **1000 ms**.
   - Network latency: **36 ms**.
   - Server receives at **1036 ms**.

3. **Server's Calculation:**
   - Time difference: **1036 ms - 40 ms = 996 ms**.
   - Since **996 ms < 1000 ms**, the server rejects the request with a **429 error**.

**Solution:**

- **Add a Latency Buffer:**
  - Introduce an additional buffer to account for latency discrepancies.
  - Adjust the Circular Buffer Check to include the buffer:
    ```python
    if now - self.__request_times[self.__curr_idx] <= 1000 + buffer:
        # ...
    ```
- **Adaptive Buffering:**
  - Dynamically adjust the buffer based on recent latency measurements.

---

### **Improved Version**

The enhanced rate limiter introduces **adaptive buffering**, dynamically adjusting the buffer based on recent latency trends to prevent 429 errors without sacrificing throughput.

```python
from collections import deque

class RateLimiter:
    def __init__(self, per_second_rate):
        self.__per_second_rate = per_second_rate
        self.__request_times = [0] * per_second_rate
        self.__curr_idx = 0

        self.__latency_window = deque(maxlen=100)  # Records the last 100 latencies
        self.__buffer = 50  # Initial buffer in milliseconds
        self.__min_buffer = 30  # Minimum buffer
        self.__max_buffer = 150  # Maximum buffer

    def update_buffer(self):
        if self.__latency_window:
            avg_latency = sum(self.__latency_window) / len(self.__latency_window)
            self.__buffer = min(self.__max_buffer, max(self.__min_buffer, int(avg_latency * 1.1)))

    def record_latency(self, latency):
        self.__latency_window.append(latency)
        self.update_buffer()
    
    @contextlib.asynccontextmanager
    async def acquire(self, timeout_ms=0):
        enter_ms = timestamp_ms()
        buffer = self.__buffer
        while True:
            now = timestamp_ms()

            if now - enter_ms > timeout_ms > 0:
                raise RateLimiterTimeout()

            if now - self.__request_times[self.__curr_idx] <= 1000 + buffer:
                sleep_time = (1000 + buffer - (now - self.__request_times[self.__curr_idx])) / 1000
                await asyncio.sleep(sleep_time)
                continue
            
            break
        
        self.__request_times[self.__curr_idx] = now
        self.__curr_idx = (self.__curr_idx + 1) % self.__per_second_rate
        yield self
```

**Key Enhancements:**

1. **Adaptive Buffering:**
   - Adjusts the buffer based on the average latency of recent requests.
   - Ensures compliance with server rate limits despite network latency variations.

2. **Dynamic Sleep Calculation:**
   - Calculates the exact sleep time needed to stay within rate limits.
   - Minimizes unnecessary delays.

**Why Adaptive Buffering Matters:**

- **Latency Variability:**
  - Network latency can fluctuate due to various factors.
- **Avoiding 429 Errors:**
  - Adjusting the buffer helps prevent the server from perceiving requests as too frequent.
- **Optimizing Throughput:**
  - Maintains high throughput by avoiding over-conservative fixed buffers.

---

### **Conclusion**

By refining the rate limiter to remove redundant checks and introducing adaptive buffering, we achieve:

1. **Improved Throughput:**
   - Increased TPS by reducing unnecessary delays and overhead.

2. **Compliance with Rate Limits:**
   - Adaptive buffering accounts for latency discrepancies, preventing 429 errors.

3. **Enhanced Flexibility:**
   - The system can handle both bursty and constant-rate traffic efficiently.

---

## **Queue Management**

### **Current Implementation**

The current implementation uses a simple queue to manage requests. Workers fetch requests from the queue and process them. However, this approach allows more requests to be generated than can be processed in a timely manner.

```python
async def exchange_facing_worker(url: str, api_key: str, queue: Queue, logger: logging.Logger):
    rate_limiter = RateLimiter(PER_SEC_RATE)
    async with aiohttp.ClientSession() as session:
        while True:
            request: Request = await queue.get()
            remaining_ttl = REQUEST_TTL_MS - (timestamp_ms() - request.create_time)

            if remaining_ttl <= 0:
                logger.warning(f"Ignoring request {request.req_id} due to expired TTL.")
                continue
            try:
                async with rate_limiter.acquire(timeout_ms=remaining_ttl):
                    async with async_timeout.timeout(1.0):
                        nonce = timestamp_ms()
                        data = {'api_key': api_key, 'nonce': nonce, 'req_id': request.req_id}
                        async with session.request('GET', url, data=data) as resp:
                            json = await resp.json()
                            if json['status'] == 'OK':
                                logger.info(f"API response: status {resp.status}, resp {json}")
                            else:
                                logger.warning(f"API response: status {resp.status}, resp {json}")
            except RateLimiterTimeout:
                logger.warning(f"Ignoring request {request.req_id} due to TTL in rate limiter.")
```

---

### **Issue**

- **Overproduction of Requests:**
  - More requests are generated than the workers can process, leading to a backlog.

- **Expired TTLs:**
  - Requests exceed their Time-To-Live (TTL) while waiting in the queue and are dropped.

- **Resource Waste:**
  - Dropped requests represent wasted computational resources and potential loss of critical operations.

---

### **Solution: Queue Manager with Dead Letter Queue (DLQ)**

To enhance request management, we introduce a **Queue Manager** that utilizes:

1. **Main Queue:**
   - Processes requests under normal operation.

2. **Dead Letter Queue (DLQ):**
   - Stores failed or timed-out requests for retry or further processing.

**Benefits:**

- **Improved Resilience:**
  - Ensures that valid requests are retried rather than discarded.

- **Prioritized Retries:**
  - Retry requests are given priority to ensure timely processing.

- **Reduced Waste:**
  - Minimizes resource waste by avoiding unnecessary dropping of requests.

#### **How Retry Requests Are Prioritized**

- **Cooldown Window for New Requests:**
  - Introducing short delays between generating new requests creates opportunities for DLQ requests to be requeued into the main queue.

- **Queue Manager's Role:**
  - Ensures that retry requests are inserted into the queue as soon as possible, without implementing a strict priority queue.

**Why This Approach Works:**

1. **Timely Execution:**
   - Retry requests are processed promptly, reducing the chance of further delays.

2. **Balanced Processing:**
   - Maintains a healthy balance between processing new and retry requests.

3. **Simplicity:**
   - Avoids the complexity of a priority queue while effectively prioritizing retries.

---

### **Lifecycle**

*[Insert UML Sequence Diagram Here]*

*Alternatively, provide a textual description of the lifecycle if the image is not available.*

---

### **Improved Version**

**Queue Manager Implementation:**

```python
class QueueManager:
    """
    Manages the main queue and DLQ with priority handling.
    """
    def __init__(self, main_queue: asyncio.Queue, dlq_queue: asyncio.Queue):
        self.main_queue = main_queue
        self.dlq_queue = dlq_queue
        self.__max_retry_count = 5
        self.__graveyard = set()

    async def add_request(self, request: Request):
        """Adds a request to the main queue."""
        await self.main_queue.put(request)

    async def requeue_from_dlq(self, benchmark: Benchmark):
        """Requeues failed requests from the DLQ back into the main queue."""
        while True:
            request: Request = await self.dlq_queue.get()
            if request.retry_count >= self.__max_retry_count:
                print(f"Request {request.req_id} has reached max retry count. Sending to graveyard.")
                self.__graveyard.add(request.req_id)
                benchmark.record_failure()
            else:
                request.retry_count += 1
                request.update_create_time()
                await self.main_queue.put(request)
            self.dlq_queue.task_done()

    async def add_to_dlq(self, request: Request):
        """Adds failed requests to the DLQ."""
        await self.dlq_queue.put(request)
```

**Updated Worker Function:**

```python
async def exchange_facing_worker(url: str, api_key: str, queue_manager: QueueManager, logger: logging.Logger, benchmark: Benchmark):
    async with aiohttp.ClientSession() as session:
        rate_limiter = RateLimiter(PER_SEC_RATE)
        while True:
            request: Request = await queue_manager.main_queue.get()
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
                            latency = timestamp_ms() - request.create_time
                            json = await resp.json()
                            if json['status'] == 'OK':
                                logger.info(f"API response: status {resp.status}, resp {json}")
                                benchmark.record_success(latency)
                            else:
                                logger.warning(f"API response: status {resp.status}, resp {json}")
            except (RateLimiterTimeout, asyncio.TimeoutError):
                logger.warning(f"Timeout for request {request.req_id}")
                await queue_manager.add_to_dlq(request)
            finally:
                queue_manager.main_queue.task_done()
```

**Updated `main` Function:**

```python
def main():
    url = "http://127.0.0.1:9999/api/request"
    loop = asyncio.get_event_loop()

    main_queue = asyncio.Queue()  # Main queue
    dlq_queue = asyncio.Queue()   # Dead Letter Queue (DLQ)
    
    queue_manager = QueueManager(main_queue, dlq_queue)  # Manage both queues

    logger = configure_logger("debug")
    benchmark = Benchmark(configure_logger("stats"))

    # Async task to generate requests
    loop.create_task(generate_requests(main_queue))

    # Async task to requeue failed requests from DLQ back into the main queue
    loop.create_task(queue_manager.requeue_from_dlq(benchmark))

    # Async task for each API key to run exchange_facing_worker
    for api_key in VALID_API_KEYS:
        loop.create_task(exchange_facing_worker(url, api_key, queue_manager, logger, benchmark))

    # Async task to print metrics 
    loop.create_task(metrics_printer(benchmark))

    # Run the event loop
    loop.run_forever()
```

**Key Changes:**

- **Retry Mechanism:**
  - Requests that fail or expire are moved to the DLQ for retry.

- **Queue Management:**
  - The Queue Manager handles requeuing of failed requests, preventing loss.

- **Benchmarking Integration:**
  - Successes and failures are recorded to monitor performance.

---

### **Caveat: Slow Rate of Consuming Requests**

Despite the improvements, the system is still limited by:

- **Limited API Keys:**
  - Only 5 API keys are available, each with its own rate limit.

- **Processing Rate Constraints:**
  - The rate at which workers can process requests is insufficient to prevent TTL expirations, especially under high load.

- **Latencies:**
  - Network and processing latencies exacerbate the issue, causing delays in request handling.

---

### **Workarounds**

To mitigate this problem, we can consider several approaches:

1. **Implementing Backpressure on `generate_requests()`:**

   - **Description:**
     - Adjust the request generation rate based on the current queue size.
     - Prevents the system from being overwhelmed by excessive incoming requests.

   - **Implementation:**
     - Modify the `generate_requests()` function to monitor the queue length and adjust sleep intervals accordingly.
     - Example:
       ```python
       async def generate_requests(queue: Queue):
           curr_req_id = 0
           MAX_QUEUE_SIZE = 500  # Threshold for backpressure
           while True:
               if queue.qsize() < MAX_QUEUE_SIZE:
                   await queue.put(Request(curr_req_id))
                   curr_req_id += 1
               else:
                   # Pause generation when queue is full
                   await asyncio.sleep(0.1)
               # Adjust sleep based on current load
               await asyncio.sleep(random.uniform(0, 0.05))
       ```

   - **Benefits:**
     - **Prevents Overloading:**
       - Keeps the queue size manageable.
     - **Aligns with Processing Capacity:**
       - Matches request generation with the system's ability to process them.

2. **Implementing Multithreading:**

   - **Description:**
     - Utilize multithreading to increase the rate of request consumption.
     - Allows multiple requests to be processed concurrently per API key, within rate limits.

   - **Considerations:**
     - **Thread Safety:**
       - Ensure shared resources like the rate limiter are thread-safe.
     - **Global Interpreter Lock (GIL):**
       - Be aware that CPU-bound tasks may not benefit due to Python's GIL.
     - **Alternative:**
       - Use multiprocessing or asynchronous programming to achieve concurrency.

   - **Benefits:**
     - **Increased Throughput:**
       - Can potentially reduce the time requests spend waiting in the queue.
     - **Better Resource Utilization:**
       - Overlaps I/O operations to make efficient use of time.

---

By incorporating these workarounds, we aim to address the root cause of the slow rate of consuming requests. Implementing backpressure helps prevent the system from being overwhelmed, while multithreading or alternative concurrency models can improve the processing rate within the constraints of available API keys and rate limits.

