# Maximizing Client Throughput

## Table of Contents

1. **[Getting Started](#getting-started)**  
2. **[Folder Structure](#folder-structure)**  
3. **[Introduction](#introduction)**  
4. **[Benchmarking System](#benchmarking-system)**  
   - [Key Metrics Tracked](#key-metrics-tracked)  
   - [How the Benchmarking System Works](#how-the-benchmarking-system-works)  
5. **[Enhancing the Rate Limiter](#enhancing-the-rate-limiter)**  
   - [Original Implementation](#original-implementation)  
   - [Identified Issues](#identified-issues)  
   - [Proposed Solution](#proposed-solution)  
   - [Observation: Performance Improvement](#observation-performance-improvement)  
   - [Potential Issue: 429 Errors Due to Latency](#potential-issue-429-errors-due-to-latency)  
   - [Improved Version: Adaptive Buffering](#improved-version-adaptive-buffering)  
6. **[Improving Queue System](#improving-queue-system)**  
   - [Current Implementation](#current-implementation)  
   - [Solution: Queue Manager with Dead Letter Queue (DLQ)](#solution-queue-manager-with-dead-letter-queue-dlq)  
   - [Lifecycle with Queue Manager](#lifecycle-with-queue-manager)  
   - [Monitoring the Queue](#monitoring-the-queue)  
7. **[Addressing the Root Cause: Bloating of the Main Queue](#addressing-the-root-cause-bloating-of-the-main-queue)**  
8. **[Exploring Multithreading](#exploring-multithreading)**  
   - [Rationale](#rationale)  
   - [Changes to the Current Code](#changes-to-the-current-code)  
9. **[Comparison Between Asyncio and Threading Client](#comparison-between-asyncio-and-threading-client)**  
   - [Baseline Comparison](#baseline-of-comparison)  
   - [Observation](#observation)  
   - [Explanation](#explanation)  
10. **[Overview and Modifications Summary](#overview)**  
    - [Modifying the Original Client for Improved Performance](#modifying-the-original-client-for-improved-performance)  
    - [Multithreading or Asynchronous?](#multithreading-or-asynchronous)  
11. **[Final Thoughts](#final-thoughts)**  



## Getting Started

```
pip install requirements.txt

# might need to restart the server each time a client is ran.

# run the server
python3 original_server.py

# run async client
python3 async/client.py

# run multithreaded client
python3 thread/client.py
```

## Folder Structure

- /async - folder for asyncio client
- /thread - folder for multi-threading client
- original_client.py - original version of the provided client
- original_server.py - original version of the provided server

## Introduction

This document provides a review and optimization of a client application aimed at maximizing throughput while adhering to server-imposed rate limits. The client interacts with a server using multiple API keys and is designed to send as many requests as possible without exceeding rate limits or causing request failures. It includes the issues identified in the existing implementation, the design choices made to address them, and the impact of these changes on performance. 

## Benchmarking System

> [!NOTE]
> Implementation of the benchmark class is inside `async/benchmark.py` and `thread/benchmark.py`

### Key Metrics Tracked

1. **Total Successful Requests**  
   - **Why:** Indicates system stability and efficiency under load.

2. **Total Failed Requests**  
   - **Why:** Identifies network or timeout issues to ensure retries and prevent bottlenecks.

3. **Average Latency (ms)**  
   - **Why:** Ensures fast response times and improves user experience.

4. **Throughput (TPS)**  
   - **Why:** Measures the system's capacity to handle high traffic without degradation.

---

### How the Benchmarking System Works

- **Recording Successful/Failed Requests:**  
  Logs successes with latencies and tracks failures to monitor stability.

- **Calculating Average Latency:**  
  Computes the mean latency of all successful requests to assess performance.

- **Calculating Throughput:**  
  Measures successful requests per second from the start of the benchmark.

- **Metrics Printing:**  
  Regularly prints key metrics for real-time feedback during execution.

## Enhancing the Rate Limiter

### Original Implementation

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
### Identified Issues

The current rate limiter has two conditional statements that introduce brief sleeps to control the rate at which requests are sent:

1. **Fixed Interval Check:** Ensures that the time interval between consecutive requests does not fall below a predefined minimum (`min_duration_ms_between_requests`).

   ```python
   if now - self.__last_request_time <= self.__min_duration_ms_between_requests:
       await asyncio.sleep(0.001)
       continue
   ```

2. **Circular Buffer Check:** Ensures that no more than a specified number of requests are sent within any 1-second window.

   ```python
   if now - self.__request_times[self.__curr_idx] <= 1000:
       await asyncio.sleep(0.001)
       continue
   ```

**Redundancy in Checks:**

- Both checks aim to control request rates.

---

### Proposed Solution


We propose removing the **Fixed Interval Check**, allowing the **Circular Buffer Check** to regulate the request rate effectively. This change reduces unnecessary context switching and improves performance. The **Circular Buffer Check** handles both bursty and constant-rate traffic efficiently.

**Importance of Handling Both Traffic Types:**

- **Bursty Traffic:** Useful in scenarios like high-frequency trading, where multiple actions need to be performed quickly within a short time frame. For example, executing a series of buy/sell actions to capitalize on rapid price movements in a volatile market.

- **Constant-Rate Traffic:** Ideal for retrieving data at regular intervals to maintain accuracy and consistency. This applies to oracle services or pricing feeds, where information must be regularly updated to reflect real-time market conditions.

---

### Observation: Performance Improvement

**Benchmark Results**:  
After removing the **Fixed Interval Check**, throughput increased from **~74 TPS to ~85 TPS**.

#### **Reasoning**:
- The **Fixed Interval Check** introduces **context switching**. Each time this check fails, the coroutine **yields control** back to the event loop, resulting in **overhead** because:
  1. The **state** of the coroutine must be saved.
  2. The **event loop switches** to another coroutine.
  3. Later, the **original coroutine's state is restored** to continue execution.
  
- Removing this check reduces **context switching and scheduling delays**, improving the efficiency of coroutine execution.

- The **Circular Buffer Check** alone ensures requests are **properly spaced across a 1-second window**, allowing the client to handle both **burst and constant-rate traffic** without unnecessary pauses. This results in smoother, **more efficient request handling**. Additionally, the server side rate limiter also uses circular buffer to track the requests, adhering to the same rate limiting rules will be optimal.

---
### Potential Issue: 429 Errors Due to Latency

While removing the **Fixed Interval Check** improves throughput, it may still lead to **429 errors**. This is due to **timestamp discrepancies** between the **client** and **server** rate limiters, caused by **incoming latency differences** on the server-side.

#### **How the Issue Occurs:**
- The **client-side rate limiter** records the difference between the **current request** and the **20th previous request** as **1000 ms (1 second)**, passing the client-side check.
- However, due to **latency variations**, the **server** might calculate the time difference as **less than 1000 ms**, causing the request to **fail the servers rate limit** and result in a **429 Error**.

#### **Example Walkthrough:**

1. **1st Request:**
   - Sent at **0 ms** from the client.
   - **Server timestamp:** Received at **40 ms** (due to **40 ms latency**).  
   - **Recorded on the server's circular buffer:** 40 ms.

2. **20th Request:**
   - Sent at **1000 ms** on the client (passes the client's rate limit).  
   - **Server timestamp:** Received at **1036 ms** (with **36 ms latency**).  
   - **Recorded on the server's circular buffer:** 1036 ms.

3. **Circular Buffer Check on the Server:**
   - **Difference between the 1st and 20th request:**  
     **1036 ms - 40 ms = 996 ms**, which is **less than 1000 ms**.

Since the **server's calculation** shows the difference as **996 ms** instead of **1000 ms**, the **server rejects the request with a 429 Error**. To prevent **429 errors**, it's important to account for **latency variability** between requests. Adding a **latency buffer** (e.g., an extra 50-100 ms delay) on the client side can help ensure that **timestamp discrepancies** don't cause requests to fail the **server's rate limiter**.

---
### Improved Version: Adaptive Buffering

```python
class RateLimiter:
    def __init__(self, per_second_rate, min_duration_ms_between_requests):
        self.__per_second_rate = per_second_rate
        self.__min_duration_ms_between_requests = min_duration_ms_between_requests
        self.__request_times = [0] * per_second_rate
        self.__curr_idx = 0

        self.__latency_window = deque(maxlen=100)  # record of the last 100 latencies
        self.__buffer = 40  # initial buffer (ms)
        self.__min_buffer = 30  # min buffer (ms)
        self.__max_buffer = 50  # max buffer (ms)

    def update_buffer(self):
        # calculate a moving average of the recent latencies
        if len(self.__latency_window) > 0:
            avg_latency = sum(self.__latency_window) / len(self.__latency_window)
            # adjust buffer based on average latency
            self.__buffer = min(self.__max_buffer, max(self.__min_buffer, int(avg_latency * 1.1)))

    def record_latency(self, latency):
        self.__latency_window.append(latency)
        self.update_buffer()
    
    @contextlib.asynccontextmanager
    async def acquire(self, timeout_ms=0):
        enter_ms = timestamp_ms()
        buffer = self.__buffer
        initial_buffer = self.__min_duration_ms_between_requests * self.__per_second_rate
        
        while True:
            now = timestamp_ms()

            if now - enter_ms > timeout_ms > 0:
                raise RateLimiterTimeout()

            if now - self.__request_times[self.__curr_idx] <= initial_buffer + buffer:
                # sleep the exact remaining time to the next second
                sleep_time = (initial_buffer + buffer - (now - self.__request_times[self.__curr_idx])) / 1000
                await asyncio.sleep(sleep_time)
                continue
            
            break
        
        self.__request_times[self.__curr_idx] = now
        self.__curr_idx = (self.__curr_idx + 1) % self.__per_second_rate
        yield self
```
> [!NOTE]
> Implementation both inside `async` and `thread` folder as `rate_limiter.py`

#### Explanation of Adaptive Buffering

This enhanced version introduces **adaptive buffering**, which fine-tunes the buffer size based on real-time **latency trends**. This ensures the system stays compliant with the server's rate limits while minimizing unnecessary delays and maximizing throughput. 


#### Key Enhancements
1. **Latency Window for Adaptive Buffering:**  
   - A **deque** stores the last **100 latencies** to track trends in request delays.
   - The **buffer size** is updated dynamically using a **moving average** of recorded latencies.
   - The formula used is:  
     ```python
     buffer = min(self.__max_buffer, max(self.__min_buffer, int(avg_latency * 1.1)))
     ```
    - This ensures the buffer size adjusts proportionally to observed latency while staying within a **safe range** (30 ms to 50 ms based on server's `MAX_LATENCY_MS`).

2. **Dynamic Sleep Calculation:**  
   - If the time between requests violates the rate limit, the **exact remaining time** needed to comply is calculated:
     ```python
     sleep_time = (initial_buffer + buffer - (now - self.__request_times[self.__curr_idx])) / 1000
     ```
   - This approach avoids **excessive sleeping** and ensures the request rate is optimized.

#### Why Adaptive Buffering Matters

Even though the **maximum server-side latency** is known (e.g., `MAX_LATENCY_MS = 50 ms`), real-world latency often varies. **Hardcoding a fixed buffer** is suboptimal, as it can either:

- **Undershoot latency**: Leading to premature requests and **429 errors**.
- **Overshoot latency**: Reducing throughput by **waiting longer than necessary**.

With **adaptive buffering**, the system continuously **learns from recent trends** and dynamically adjusts the buffer to balance **performance and compliance**.

---

### Conclusion

1. **Remove** Fix Interval Checks and only **keeping** the Circular Buffer Checks
2. Account for the server's latency and dynamically adjusts it for additional buffer when sending requests, to prevent rate limiting errors (429). 


## Improving Queue System

### Current Implementation
```python
async def exchange_facing_worker(url: str, api_key: str, queue: Queue, logger: logging.Logger):
    rate_limiter = RateLimiter(PER_SEC_RATE, DURATION_MS_BETWEEN_REQUESTS)
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
### Issue

The current implementation allows **more requests to be generated** than the client can process. This leads to **expired TTLs** for unprocessed requests in the queue. When the `remaining_ttl` drops below 0 or when there is a **request timeout**, the request is **dropped**. While this prevents the queue from becoming clogged, it also results in **wasted resources** and **lost requests** that could have been retried.


---
### Solution: Queue Manager with Dead Letter Queue (DLQ)

To improve request management, we introduce a **Queue Manager** that utilizes:
1. **Main Queue:** Processes requests under normal operation.
2. **Dead Letter Queue (DLQ):** Stores failed or timed-out requests for **retry** or further processing. This helps ensure that no valid request is wasted, even if it initially fails or exceeds its TTL.  


> [!NOTE]
> See implementation at `async/queue_manager.py`


This strategy improves **resilience** by providing better queue state management. Requests are **re-prioritized** from the DLQ, minimizing dropped requests and ensuring all requests receive multiple attempts before being discarded. In the event that the request hits the max retry limit, we will store the `req_id` for manual processing and to prevent sending redundant request, as we can assume that these requests are invalid. 

#### How Do Retry Requests Get Prioritized Over New Requests?
- **Cooldown Window for New Requests:**  
   When new requests are generated, a **short cooldown period** is introduced between them. This window allows the **Queue Manager** to **re-slot retry requests** into the queue before more new requests are created.

- **Not a Strict Priority Queue:**  
   While this is not a strict **priority queue** (where retry requests are inserted at the front), the **Queue Manager** ensures that **retry requests are slotted into the queue as soon as possible**, taking advantage of gaps in new request generation. 

This design ensures that **retry requests** are **handled promptly** without requiring a complex priority queue, optimizing for both **simplicity** and **timeliness**.

---
### Lifecycle with Queue Manager
![New Sequence with Queue Manager](./img/uml_seq_diagram.png)

---
### Monitoring the Queue 

Using the below metrics we are able to get the current state of the DLQ and Main Queue to check if there is an existing bottleneck in processing the requests inside the queue, allowing us to check the risk of the requests inside the main queue timing out due to expired TTL. 

  - **Queue Monitoring**: Track main queue and DLQ sizes, processing rates, and retry statistics.

  - **Queue Sizes**: Monitor and log the sizes of the main queue and DLQ at regular intervals.

  - **Graveyard Metrics**: Monitor the number of requests that exceed the maximum retry limit and are moved to the graveyard.


```
--- Accumulated Benchmark Metrics ---
2024-10-20 16:40:49,644 - stats - INFO - Elapsed Time: 5.00 seconds
2024-10-20 16:40:49,644 - stats - INFO - Total Successful Requests: 414
2024-10-20 16:40:49,644 - stats - INFO - Total Failed Requests: 0
2024-10-20 16:40:49,644 - stats - INFO - Total Throughput: 82.78 req/sec
2024-10-20 16:40:49,644 - stats - INFO - Average Latency: 207.68 ms
2024-10-20 16:40:49,644 - stats - INFO - Queue Sizes - Main: 26, DLQ: 0, Graveyard: 0
2024-10-20 16:40:49,644 - stats - INFO - Average Queue Sizes - Main: 13.00, DLQ: 0.00
2024-10-20 16:40:54,645 - stats - INFO - 
--- Accumulated Benchmark Metrics ---
2024-10-20 16:40:54,645 - stats - INFO - Elapsed Time: 10.00 seconds
2024-10-20 16:40:54,645 - stats - INFO - Total Successful Requests: 834
2024-10-20 16:40:54,645 - stats - INFO - Total Failed Requests: 0
2024-10-20 16:40:54,645 - stats - INFO - Total Throughput: 83.38 req/sec
2024-10-20 16:40:54,645 - stats - INFO - Average Latency: 322.23 ms
2024-10-20 16:40:54,645 - stats - INFO - Queue Sizes - Main: 40, DLQ: 0, Graveyard: 0
2024-10-20 16:40:54,645 - stats - INFO - Average Queue Sizes - Main: 22.00, DLQ: 0.00
2024-10-20 16:40:59,646 - stats - INFO - 
--- Accumulated Benchmark Metrics ---
2024-10-20 16:40:59,646 - stats - INFO - Elapsed Time: 15.00 seconds
2024-10-20 16:40:59,646 - stats - INFO - Total Successful Requests: 1262
2024-10-20 16:40:59,646 - stats - INFO - Total Failed Requests: 0
2024-10-20 16:40:59,646 - stats - INFO - Total Throughput: 84.12 req/sec
2024-10-20 16:40:59,646 - stats - INFO - Average Latency: 430.42 ms
2024-10-20 16:40:59,646 - stats - INFO - Queue Sizes - Main: 97, DLQ: 0, Graveyard: 0 
2024-10-20 16:40:59,646 - stats - INFO - Average Queue Sizes - Main: 40.75, DLQ: 0.00
2024-10-20 16:41:04,647 - stats - INFO - 
```


---
### Addressing the Root Cause: Bloating of the Main Queue

- Currently, the **rate of generating requests exceeds the rate of dequeuing requests**, causing **queue bloating** and leading to **TTL expirations**. We are limited to only **5 API keys**, each associated with a worker consuming requests from the queue. This restriction results in a processing rate that is too slow to handle the incoming traffic effectively, causing requests to expire before being processed. 

- The situation is further exacerbated by **latencies on both the client and server sides** due to rate limiting constraints, which slow down the consumption of requests from the queue. While the **Dead Letter Queue (DLQ)** allows failed requests to be retried, **it does not address the root issue**: the imbalance between the rate of request generation and processing.

To mitigate this problem, we can consider several workarounds:

1. **Implementing Backpressure on `generate_requests()`:** By controlling the rate at which requests are generated based on the current state of the queue, we can prevent the system from being overwhelmed. This approach assumes that we are able to modify the `generate_requests()` function.
    ```python
      # Apply backpressure if the queue is full
    if queue.qsize() >= max_queue_size:
        print(f"Queue is full ({queue.qsize()}), pausing request generation.")
        await asyncio.sleep(0.5)  # Wait before checking again
        continue
    ```

2. **Implementing Multithreading:** By implementing multithreading, we are able to better manage the rate of consumption of the request, resolving the issue of the expired requests TTL. 


## Exploring Multithreading 

### Rationale

To **manage the rate of request processing** and **reduce TTL expirations**, multithreading can be introduced. When have multiple workers accessing the same queue we can increase the deque rate of the requests and prevent overloading the main queue.

---
### Changes to the current code

1. Implement multithreading process to run:
    - Request Generator (Assuming we are able to change the `generate_requests()` function)
    - Metrics Printing
    - Exchange Facing Workers

2. Implement Locking on Shared Resources
    - Queue Manager - Since all the workers are sharing the same queue, there will be resource contention on the graveyard. 
    - Rate Limiter (Thread safe) - Assuming we are running multiple threads for the same API Key. 

3. Getting Unique Nonces when creating request.
    -  This is needed because currently the nonce used is timestamp-based, however there will potentially be multiple requests being sent at the same time resulting in bad nonces. 

> [!NOTE]
> See `thread` folder for implementation

## Comparison between Asyncio and Threading Client

This is the comparsion between my current implementation of the client using asynchronous and multi-threading. 

### Baseline of comparison

**Asyncio**:
1. 5 Coroutines, 1 for each API Key
2. 1 Coroutine to generate requests
3. 1 Coroutine Queue Manager to requeue from DLQ
4. 2 Coroutines for monitoring and benchmarking

**Multithreading**:
1. 5 Threads, 1 for each API Key
2. 1 Thread to generate requests
3. 1 Thread for Queue Manager to requeue from DLQ
4. 2 Threads for monitoring and benchmarking

---
### Observation
```
ASYNCIO STATS
--- Accumulated Benchmark Metrics ---
2024-10-20 22:09:30,164 - stats - INFO - Elapsed Time: 15.00 seconds
2024-10-20 22:09:30,164 - stats - INFO - Total Successful Requests: 1253
2024-10-20 22:09:30,164 - stats - INFO - Total Failed Requests: 0
2024-10-20 22:09:30,164 - stats - INFO - Total Throughput: 83.51 req/sec
2024-10-20 22:09:30,164 - stats - INFO - Average Latency: 587.35 ms
2024-10-20 22:09:30,164 - stats - INFO - Queue Sizes - Main: 120, DLQ: 0, Graveyard: 0
2024-10-20 22:09:30,164 - stats - INFO - Average Queue Sizes - Main: 55.75, DLQ: 0.00
2024-10-20 22:09:35,165 - stats - INFO - 
--- Accumulated Benchmark Metrics ---
2024-10-20 22:09:35,165 - stats - INFO - Elapsed Time: 20.00 seconds
2024-10-20 22:09:35,165 - stats - INFO - Total Successful Requests: 1680
2024-10-20 22:09:35,165 - stats - INFO - Total Failed Requests: 9
2024-10-20 22:09:35,165 - stats - INFO - Total Throughput: 83.98 req/sec
2024-10-20 22:09:35,165 - stats - INFO - Average Latency: 700.96 ms
2024-10-20 22:09:35,165 - stats - INFO - Queue Sizes - Main: 131, DLQ: 0, Graveyard: 9
2024-10-20 22:09:35,165 - stats - INFO - Average Queue Sizes - Main: 70.80, DLQ: 0.00
2024-10-20 22:09:40,166 - stats - INFO - 
--- Accumulated Benchmark Metrics ---
2024-10-20 22:09:40,166 - stats - INFO - Elapsed Time: 25.01 seconds
2024-10-20 22:09:40,166 - stats - INFO - Total Successful Requests: 2102
2024-10-20 22:09:40,166 - stats - INFO - Total Failed Requests: 13
2024-10-20 22:09:40,166 - stats - INFO - Total Throughput: 84.06 req/sec
2024-10-20 22:09:40,166 - stats - INFO - Average Latency: 769.45 ms
2024-10-20 22:09:40,166 - stats - INFO - Queue Sizes - Main: 136, DLQ: 0, Graveyard: 13
2024-10-20 22:09:40,166 - stats - INFO - Average Queue Sizes - Main: 81.67, DLQ: 0.00
```
```
THREADING STATS
--- Accumulated Benchmark Metrics ---
2024-10-21 20:38:29,345 - stats - INFO - Elapsed Time: 15.00 seconds
2024-10-21 20:38:29,345 - stats - INFO - Total Successful Requests: 1163
2024-10-21 20:38:29,345 - stats - INFO - Total Failed Requests: 0
2024-10-21 20:38:29,345 - stats - INFO - Total Throughput: 77.45 req/sec
2024-10-21 20:38:29,345 - stats - INFO - Average Latency (Total): 76.11 ms
2024-10-21 20:38:34,349 - stats - INFO - Queue Sizes - Main: 0, DLQ: 0, Graveyard: 0
2024-10-21 20:38:34,350 - stats - INFO - Average Queue Sizes - Main: 0.60, DLQ: 0.00
2024-10-21 20:38:34,349 - stats - INFO - 
--- Accumulated Benchmark Metrics ---
2024-10-21 20:38:34,350 - stats - INFO - Elapsed Time: 20.00 seconds
2024-10-21 20:38:34,350 - stats - INFO - Total Successful Requests: 1543
2024-10-21 20:38:34,350 - stats - INFO - Total Failed Requests: 0
2024-10-21 20:38:34,350 - stats - INFO - Total Throughput: 77.07 req/sec
2024-10-21 20:38:34,350 - stats - INFO - Average Latency (Total): 74.29 ms
2024-10-21 20:38:39,352 - stats - INFO - Queue Sizes - Main: 3, DLQ: 0, Graveyard: 0
2024-10-21 20:38:39,352 - stats - INFO - Average Queue Sizes - Main: 1.00, DLQ: 0.00
2024-10-21 20:38:39,356 - stats - INFO - 
--- Accumulated Benchmark Metrics ---
2024-10-21 20:38:39,356 - stats - INFO - Elapsed Time: 25.00 seconds
2024-10-21 20:38:39,356 - stats - INFO - Total Successful Requests: 1939
2024-10-21 20:38:39,356 - stats - INFO - Total Failed Requests: 0
2024-10-21 20:38:39,356 - stats - INFO - Total Throughput: 77.48 req/sec
2024-10-21 20:38:39,356 - stats - INFO - Average Latency (Total): 75.12 ms
2024-10-21 20:38:44,356 - stats - INFO - Queue Sizes - Main: 1, DLQ: 0, Graveyard: 0
2024-10-21 20:38:44,356 - stats - INFO - Average Queue Sizes - Main: 1.00, DLQ: 0.00
2024-10-21 20:38:44,361 - stats - INFO - 
```

The **Asyncio client (~84 TPS)** outperforms the **Threading client (~77 TPS)** in terms of throughput (TPS). This higher throughput is primarily due to asyncio's ability to efficiently handle concurrent I/O-bound operations. However, **more requests accumulate inside the queue in the Asyncio client**, leading to **TTL expirations**. 

In contrast, the Threading client has **multiple threads running workers that are dequeuing the requests from the same queue simultaneously**. Furthermore the locking mechanism on the Queue will help to throttle the rate that requests are added, if the `get` is acquired the `put_nowait` cannot occur likewise for the other side.

---
#### Explanation

1. **Concurrency Models: Cooperative vs. Preemptive Multitasking**

   - **Asyncio (Cooperative Multitasking):**
     - Utilizes a **single event loop** to manage all coroutines.
     - Coroutines yield control voluntarily using `await`.
     - **Limitation:** If a coroutine takes longer than expected or fails to yield, it can **block the event loop**, delaying other coroutines.
     - **Impact:** Requests wait longer in the queue, increasing the likelihood of TTL expirations.

   - **Threading (Preemptive Multitasking):**
     - Employs **multiple threads** that the operating system schedules independently.
     - Threads can run in parallel and are preempted as needed.
     - **Benefit:** Even if one thread is blocked (e.g., due to I/O), other threads continue processing.
     - **Impact:** Requests are dequeued and processed promptly, reducing TTL expirations.

2. **Queue Drain Speed and TTL Expirations**

   - **Asyncio Client:**
     - **Single Event Loop Bottleneck:** Under heavy load, the event loop may become overwhelmed.
     - **Result:** Queue grows as requests are generated faster than they are processed.
     - **Consequence:** Increased **TTL expirations** due to delayed processing.

   - **Threading Client:**
     - **Concurrent Dequeuing:** Multiple threads access and dequeue from the queue simultaneously.
     - **Result:** Queue remains small as requests are processed quickly.
     - **Consequence:** **Fewer TTL expirations** since requests are handled within their valid time frame.

3. **Network I/O Latency and Blocking Scenarios**

   - **Asyncio Client:**
     - **Event Loop Sensitivity:** If a coroutine encounters network latency and doesn't yield promptly, it can block the event loop.
     - **Impact:** Other coroutines are prevented from running, delaying request processing.
     - **Result:** Accumulation of requests in the queue and potential TTL expirations.

   - **Threading Client:**
     - **Independent Threads:** A thread waiting on network I/O doesn't block other threads.
     - **Impact:** Other threads continue to process requests without interruption.
     - **Result:** Continuous queue draining and timely request handling.

4. **Task Switching Overhead in Asyncio**

   - **Asyncio Client:**
     - **Context Switching Overhead:** Frequent switching between many coroutines can introduce latency.
     - **Event Loop Saturation:** High task-switching demands can overwhelm the event loop.
     - **Result:** Delays in processing coroutines, leading to more requests accumulating in the queue.

   - **Threading Client:**
     - **OS-Level Scheduling:** Threads are managed by the operating system, which can efficiently handle context switching between threads.
     - **Reduced Overhead:** Although threads have their own overhead, the impact on queue processing is less pronounced.
     - **Result:** Maintains a low queue size with minimal TTL expirations.

---
#### Summary

- **Asyncio Client:**
  - **Pros:** Higher throughput due to efficient non-blocking I/O operations.
  - **Cons:** Struggles with queue management under heavy load, leading to more TTL expirations as requests wait longer to be processed.

- **Threading Client:**
  - **Pros:** Better at quickly draining the queue with multiple threads, resulting in timely processing and fewer TTL expirations.
  - **Cons:** Slightly lower throughput due to the overhead associated with thread management and context switching.


## Overview

### Modifying the Original Client for Improved Performance
To enhance throughput and optimize request management, the original client was modified by:

1. **Removing Redundant Waits:**
   - The **Fixed Interval Check** was removed to avoid unnecessary pauses that caused context switching and degraded performance. This resulted in improved request throughput, as the **Circular Buffer Check** alone provided sufficient rate limiting.

2. **Introducing a Queue Manager:**
   - A **Queue Manager** was added to manage both the **Main Queue** and **Dead Letter Queue (DLQ)**. This ensures that **expired or failed requests** are not lost but **retried** through the DLQ, preventing unnecessary drops.
   - **Graveyard Tracking** was also implemented to handle requests that exceed the **maximum retry count**, ensuring manual handling and preventing repeated invalid requests.
   - With **prioritization of DLQ requests** over new requests, we ensured that time-sensitive retries are given preference without introducing complex priority queue logic.

### Multithreading or Asynchronous? 

- **Trade-off Between Throughput and Request Delivery Guarantee:**
  - In an **asynchronous (asyncio)** model, we prioritize **throughput** by leveraging non-blocking I/O operations. This allows us to achieve **higher TPS** (Transactions Per Second) by efficiently switching between multiple tasks. However, the downside is that **requests may accumulate** in the queue, resulting in **TTL expirations** if the event loop is saturated or if network latencies are high. This design favors **high-performance, bursty workloads**, such as high-frequency trading, but may compromise on ensuring all requests are successfully processed.
  
  - On the other hand, **multithreading** offers better **guarantee of request delivery** by running multiple threads concurrently, each working in parallel to dequeue and process requests from the shared queue. This prevents queue bloating and minimizes TTL expirations, ensuring that most requests are sent in a timely manner. However, the **thread overhead** and **context-switching latency** result in **lower overall throughput** compared to the asyncio model.

- **Threading Client:**  
  - Recommended when **reliability** is critical, and **each request must be processed successfully** within its TTL.  
  - Best suited for **high-stakes transactional systems** (e.g., order books, payment gateways) where the cost of failed or dropped requests is high.  
  - Although slightly lower in throughput (~8 TPS), the threading client ensures **faster queue draining** and **fewer TTL expirations** due to **parallel execution**.

- **Asyncio Client:**  
  - Optimal for scenarios where **throughput and performance** are prioritized over strict delivery guarantees.  
  - With **regulated request generation** or **backpressure mechanisms**, the asyncio client can effectively handle **bursty workloads** and achieve **higher throughput** with minimal overhead.  
  - Ideal for systems that need to **poll APIs frequently** (e.g., data ingestion services, real-time monitoring) and can tolerate occasional **TTL expirations**.

---
### Final Thoughts

Based on the current **implementation and constraints**, I believe the **threading client** offers a more well-rounded solution. Although it sacrifices around **~8 TPS** compared to the asyncio client, it provides a more **reliable rate of delivery**. In contrast, the **asyncio client**, while capable of achieving **higher throughput**, is more prone to **TTL expirations** and **request failures** due to the inherent challenges of **queue management and task scheduling** in asynchronous environments.

However, if **request generation** can be **regulated**—either through **adaptive rate control** or **backpressure mechanisms**—the asyncio client becomes the more **optimal option**. This is because it handles **high-concurrency I/O-bound workloads** more efficiently and incurs less overhead compared to threading.