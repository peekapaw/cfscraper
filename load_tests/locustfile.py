"""
Main Locust load testing file for CFScraper API.

This file provides comprehensive load testing scenarios including:
- Realistic user behavior simulation
- Different load patterns (steady, spike, stress)
- Performance metrics collection
- Load test result analysis and reporting
"""

import json
import random
import time
from typing import Dict, Any, List
from urllib.parse import urljoin

from locust import HttpUser, task, between, events
from locust.env import Environment
from locust.stats import stats_printer, stats_history
from locust.log import setup_logging

# Test data for realistic scenarios
TEST_URLS = [
    "https://httpbin.org/get",
    "https://httpbin.org/json",
    "https://httpbin.org/user-agent",
    "https://httpbin.org/headers",
    "https://httpbin.org/ip",
    "https://example.com",
    "https://jsonplaceholder.typicode.com/posts/1",
    "https://jsonplaceholder.typicode.com/users/1",
]

SCRAPER_TYPES = ["cloudscraper", "selenium"]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:89.0) Gecko/20100101 Firefox/89.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:89.0) Gecko/20100101 Firefox/89.0",
]


class CFScraperUser(HttpUser):
    """Base user class for CFScraper API load testing"""
    
    wait_time = between(1, 5)  # Wait 1-5 seconds between tasks
    
    def on_start(self):
        """Called when a user starts"""
        self.api_key = None
        self.active_jobs = []
        self.completed_jobs = []
        
        # Authenticate and get API key (if authentication is implemented)
        # self.authenticate()
    
    def authenticate(self):
        """Authenticate user and get API key"""
        # This would be implemented if authentication is required
        pass
    
    @task(3)
    def submit_scraping_job(self):
        """Submit a new scraping job - most common operation"""
        url = random.choice(TEST_URLS)
        scraper_type = random.choice(SCRAPER_TYPES)
        
        job_data = {
            "url": url,
            "method": "GET",
            "scraper_type": scraper_type,
            "headers": {
                "User-Agent": random.choice(USER_AGENTS)
            },
            "tags": [f"load_test_{random.randint(1, 100)}"],
            "priority": random.randint(1, 10)
        }
        
        with self.client.post(
            "/api/scraper/scrape",
            json=job_data,
            headers={"Content-Type": "application/json"},
            catch_response=True
        ) as response:
            if response.status_code == 200:
                try:
                    result = response.json()
                    task_id = result.get("task_id")
                    if task_id:
                        self.active_jobs.append(task_id)
                        response.success()
                    else:
                        response.failure("No task_id in response")
                except json.JSONDecodeError:
                    response.failure("Invalid JSON response")
            else:
                response.failure(f"HTTP {response.status_code}")
    
    @task(2)
    def check_job_status(self):
        """Check status of active jobs"""
        if not self.active_jobs:
            return
        
        task_id = random.choice(self.active_jobs)
        
        with self.client.get(
            f"/api/jobs/{task_id}",
            catch_response=True
        ) as response:
            if response.status_code == 200:
                try:
                    result = response.json()
                    status = result.get("status")
                    
                    if status in ["completed", "failed", "cancelled"]:
                        self.active_jobs.remove(task_id)
                        self.completed_jobs.append(task_id)
                    
                    response.success()
                except json.JSONDecodeError:
                    response.failure("Invalid JSON response")
            elif response.status_code == 404:
                # Job not found, remove from active list
                self.active_jobs.remove(task_id)
                response.success()
            else:
                response.failure(f"HTTP {response.status_code}")
    
    @task(1)
    def list_jobs(self):
        """List jobs with pagination"""
        params = {
            "page": random.randint(1, 5),
            "page_size": random.choice([10, 20, 50]),
            "sort_by": random.choice(["created_at", "priority", "status"]),
            "sort_order": random.choice(["asc", "desc"])
        }
        
        # Randomly add filters
        if random.random() < 0.3:
            params["status"] = random.choice(["queued", "running", "completed", "failed"])
        
        if random.random() < 0.2:
            params["scraper_type"] = random.choice(SCRAPER_TYPES)
        
        with self.client.get(
            "/api/jobs/",
            params=params,
            catch_response=True
        ) as response:
            if response.status_code == 200:
                try:
                    result = response.json()
                    if "jobs" in result:
                        response.success()
                    else:
                        response.failure("Invalid response format")
                except json.JSONDecodeError:
                    response.failure("Invalid JSON response")
            else:
                response.failure(f"HTTP {response.status_code}")
    
    @task(1)
    def search_jobs(self):
        """Search jobs with advanced filters"""
        search_data = {
            "query": random.choice(["httpbin", "example", "test"]),
            "page": random.randint(1, 3),
            "page_size": 20,
            "filters": {}
        }
        
        # Add random filters
        if random.random() < 0.4:
            search_data["filters"]["status"] = random.choice(["completed", "failed"])
        
        if random.random() < 0.3:
            search_data["filters"]["scraper_type"] = random.choice(SCRAPER_TYPES)
        
        with self.client.post(
            "/api/jobs/search",
            json=search_data,
            headers={"Content-Type": "application/json"},
            catch_response=True
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"HTTP {response.status_code}")
    
    @task(1)
    def health_check(self):
        """Check API health"""
        with self.client.get("/health", catch_response=True) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"HTTP {response.status_code}")
    
    @task(1)
    def get_metrics(self):
        """Get system metrics"""
        with self.client.get("/metrics", catch_response=True) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"HTTP {response.status_code}")


class LightUser(CFScraperUser):
    """Light user with minimal activity"""
    wait_time = between(5, 15)
    weight = 3
    
    @task(5)
    def submit_scraping_job(self):
        super().submit_scraping_job()
    
    @task(2)
    def check_job_status(self):
        super().check_job_status()
    
    @task(1)
    def health_check(self):
        super().health_check()


class HeavyUser(CFScraperUser):
    """Heavy user with high activity"""
    wait_time = between(0.5, 2)
    weight = 1
    
    @task(10)
    def submit_scraping_job(self):
        super().submit_scraping_job()
    
    @task(5)
    def check_job_status(self):
        super().check_job_status()
    
    @task(3)
    def list_jobs(self):
        super().list_jobs()
    
    @task(2)
    def search_jobs(self):
        super().search_jobs()


class BurstUser(CFScraperUser):
    """User that creates bursts of activity"""
    wait_time = between(10, 30)
    weight = 1
    
    @task
    def burst_activity(self):
        """Create a burst of requests"""
        burst_size = random.randint(5, 15)
        
        for _ in range(burst_size):
            self.submit_scraping_job()
            time.sleep(random.uniform(0.1, 0.5))
        
        # Check some job statuses
        for _ in range(min(3, len(self.active_jobs))):
            self.check_job_status()
            time.sleep(random.uniform(0.1, 0.3))


# Event handlers for custom metrics collection
@events.request.add_listener
def on_request(request_type, name, response_time, response_length, exception, context, **kwargs):
    """Custom request handler for additional metrics"""
    if exception:
        print(f"Request failed: {name} - {exception}")


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Called when test starts"""
    print("Load test starting...")
    print(f"Target host: {environment.host}")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Called when test stops"""
    print("Load test completed!")
    
    # Print summary statistics
    stats = environment.stats
    print(f"\nTest Summary:")
    print(f"Total requests: {stats.total.num_requests}")
    print(f"Total failures: {stats.total.num_failures}")
    print(f"Average response time: {stats.total.avg_response_time:.2f}ms")
    print(f"Max response time: {stats.total.max_response_time:.2f}ms")
    print(f"Requests per second: {stats.total.current_rps:.2f}")
    
    if stats.total.num_requests > 0:
        failure_rate = (stats.total.num_failures / stats.total.num_requests) * 100
        print(f"Failure rate: {failure_rate:.2f}%")


# Custom load shapes for different test scenarios
from locust import LoadTestShape

class StepLoadShape(LoadTestShape):
    """Step load pattern - gradually increase users"""

    step_time = 30  # seconds
    step_load = 10  # users per step
    spawn_rate = 2  # users per second
    time_limit = 300  # total test time

    def tick(self):
        run_time = self.get_run_time()

        if run_time > self.time_limit:
            return None

        current_step = run_time // self.step_time
        return (current_step * self.step_load, self.spawn_rate)


class SpikeLoadShape(LoadTestShape):
    """Spike load pattern - sudden increases in load"""

    def tick(self):
        run_time = self.get_run_time()

        if run_time < 60:
            return (10, 2)
        elif run_time < 120:
            return (50, 10)  # Spike
        elif run_time < 180:
            return (10, 2)   # Back to normal
        elif run_time < 240:
            return (100, 20) # Bigger spike
        elif run_time < 300:
            return (10, 2)   # Back to normal
        else:
            return None


class StressLoadShape(LoadTestShape):
    """Stress test pattern - high sustained load"""

    def tick(self):
        run_time = self.get_run_time()

        if run_time < 60:
            return (20, 5)   # Warm up
        elif run_time < 300:
            return (100, 10) # High load
        elif run_time < 360:
            return (20, 5)   # Cool down
        else:
            return None


# To use a specific load shape, run:
# locust -f locustfile.py --host=http://localhost:8000 StepLoadShape
# locust -f locustfile.py --host=http://localhost:8000 SpikeLoadShape
# locust -f locustfile.py --host=http://localhost:8000 StressLoadShape
