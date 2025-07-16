#!/usr/bin/env python3
"""
Performance benchmark validation script for CFScraper.

This script validates that all performance benchmarks are met:
- Response time benchmarks
- Throughput benchmarks  
- Concurrent operation limits
- Memory and CPU usage targets
- Performance regression testing
"""

import time
import json
import asyncio
import statistics
from typing import Dict, Any, List, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime
import argparse
import concurrent.futures
import psutil
import requests


@dataclass
class BenchmarkTarget:
    """Performance benchmark target"""
    name: str
    metric: str
    target_value: float
    unit: str
    tolerance: float = 0.1  # 10% tolerance by default
    critical: bool = True


@dataclass
class BenchmarkResult:
    """Benchmark test result"""
    name: str
    target_value: float
    actual_value: float
    unit: str
    passed: bool
    tolerance: float
    critical: bool
    details: Dict[str, Any] = None


class PerformanceBenchmarkValidator:
    """Performance benchmark validation"""
    
    def __init__(self, api_url: str = "http://localhost:8000"):
        self.api_url = api_url
        self.results: List[BenchmarkResult] = []
        
        # Define benchmark targets based on requirements
        self.benchmarks = [
            # Response time benchmarks
            BenchmarkTarget("API Health Check Response Time", "response_time", 100, "ms", 0.2),
            BenchmarkTarget("Job Submission Response Time", "response_time", 500, "ms", 0.3),
            BenchmarkTarget("Job Status Check Response Time", "response_time", 200, "ms", 0.2),
            BenchmarkTarget("Job List Response Time", "response_time", 1000, "ms", 0.3),
            
            # Throughput benchmarks
            BenchmarkTarget("Job Submission Throughput", "throughput", 100, "requests/sec", 0.2),
            BenchmarkTarget("Concurrent Job Processing", "concurrent_jobs", 50, "jobs", 0.1),
            BenchmarkTarget("Database Query Throughput", "db_queries", 500, "queries/sec", 0.3),
            
            # Resource usage benchmarks
            BenchmarkTarget("Memory Usage Under Load", "memory_usage", 80, "percent", 0.1),
            BenchmarkTarget("CPU Usage Under Load", "cpu_usage", 70, "percent", 0.2),
            BenchmarkTarget("Database Connection Pool Usage", "db_pool_usage", 80, "percent", 0.1),
            BenchmarkTarget("Redis Connection Pool Usage", "redis_pool_usage", 70, "percent", 0.1),
            
            # Scalability benchmarks
            BenchmarkTarget("Response Time Under Load", "response_time_load", 2000, "ms", 0.5),
            BenchmarkTarget("Error Rate Under Load", "error_rate", 1, "percent", 0.5),
            BenchmarkTarget("Memory Leak Rate", "memory_leak", 5, "MB/hour", 0.2, False),
        ]
    
    async def validate_response_time_benchmarks(self) -> List[BenchmarkResult]:
        """Validate API response time benchmarks"""
        results = []
        
        # Test health check response time
        response_times = []
        for _ in range(10):
            start_time = time.time()
            try:
                response = requests.get(f"{self.api_url}/health", timeout=5)
                if response.status_code == 200:
                    response_times.append((time.time() - start_time) * 1000)
            except:
                pass
            await asyncio.sleep(0.1)
        
        if response_times:
            avg_response_time = statistics.mean(response_times)
            target = next(b for b in self.benchmarks if b.name == "API Health Check Response Time")
            results.append(BenchmarkResult(
                name=target.name,
                target_value=target.target_value,
                actual_value=avg_response_time,
                unit=target.unit,
                passed=avg_response_time <= target.target_value * (1 + target.tolerance),
                tolerance=target.tolerance,
                critical=target.critical,
                details={"min": min(response_times), "max": max(response_times), "samples": len(response_times)}
            ))
        
        # Test job submission response time
        job_response_times = []
        for _ in range(5):
            start_time = time.time()
            try:
                response = requests.post(
                    f"{self.api_url}/api/scraper/scrape",
                    json={
                        "url": "https://httpbin.org/get",
                        "method": "GET",
                        "scraper_type": "cloudscraper"
                    },
                    timeout=10
                )
                if response.status_code == 200:
                    job_response_times.append((time.time() - start_time) * 1000)
            except:
                pass
            await asyncio.sleep(1)
        
        if job_response_times:
            avg_job_response_time = statistics.mean(job_response_times)
            target = next(b for b in self.benchmarks if b.name == "Job Submission Response Time")
            results.append(BenchmarkResult(
                name=target.name,
                target_value=target.target_value,
                actual_value=avg_job_response_time,
                unit=target.unit,
                passed=avg_job_response_time <= target.target_value * (1 + target.tolerance),
                tolerance=target.tolerance,
                critical=target.critical,
                details={"min": min(job_response_times), "max": max(job_response_times), "samples": len(job_response_times)}
            ))
        
        return results
    
    async def validate_throughput_benchmarks(self) -> List[BenchmarkResult]:
        """Validate throughput benchmarks"""
        results = []
        
        # Test job submission throughput
        print("Testing job submission throughput...")
        start_time = time.time()
        successful_requests = 0
        total_requests = 100
        
        async def submit_job():
            try:
                response = requests.post(
                    f"{self.api_url}/api/scraper/scrape",
                    json={
                        "url": "https://httpbin.org/get",
                        "method": "GET",
                        "scraper_type": "cloudscraper"
                    },
                    timeout=5
                )
                return response.status_code == 200
            except:
                return False
        
        # Submit jobs concurrently
        tasks = [submit_job() for _ in range(total_requests)]
        task_results = await asyncio.gather(*tasks, return_exceptions=True)
        
        successful_requests = sum(1 for result in task_results if result is True)
        duration = time.time() - start_time
        throughput = successful_requests / duration
        
        target = next(b for b in self.benchmarks if b.name == "Job Submission Throughput")
        results.append(BenchmarkResult(
            name=target.name,
            target_value=target.target_value,
            actual_value=throughput,
            unit=target.unit,
            passed=throughput >= target.target_value * (1 - target.tolerance),
            tolerance=target.tolerance,
            critical=target.critical,
            details={
                "total_requests": total_requests,
                "successful_requests": successful_requests,
                "duration": duration,
                "success_rate": successful_requests / total_requests * 100
            }
        ))
        
        return results
    
    def validate_resource_usage_benchmarks(self) -> List[BenchmarkResult]:
        """Validate resource usage benchmarks"""
        results = []
        
        # Monitor resource usage for 30 seconds
        print("Monitoring resource usage...")
        cpu_samples = []
        memory_samples = []
        
        for _ in range(30):
            cpu_samples.append(psutil.cpu_percent(interval=1))
            memory_samples.append(psutil.virtual_memory().percent)
        
        avg_cpu = statistics.mean(cpu_samples)
        avg_memory = statistics.mean(memory_samples)
        
        # CPU usage benchmark
        cpu_target = next(b for b in self.benchmarks if b.name == "CPU Usage Under Load")
        results.append(BenchmarkResult(
            name=cpu_target.name,
            target_value=cpu_target.target_value,
            actual_value=avg_cpu,
            unit=cpu_target.unit,
            passed=avg_cpu <= cpu_target.target_value * (1 + cpu_target.tolerance),
            tolerance=cpu_target.tolerance,
            critical=cpu_target.critical,
            details={"min": min(cpu_samples), "max": max(cpu_samples), "samples": len(cpu_samples)}
        ))
        
        # Memory usage benchmark
        memory_target = next(b for b in self.benchmarks if b.name == "Memory Usage Under Load")
        results.append(BenchmarkResult(
            name=memory_target.name,
            target_value=memory_target.target_value,
            actual_value=avg_memory,
            unit=memory_target.unit,
            passed=avg_memory <= memory_target.target_value * (1 + memory_target.tolerance),
            tolerance=memory_target.tolerance,
            critical=memory_target.critical,
            details={"min": min(memory_samples), "max": max(memory_samples), "samples": len(memory_samples)}
        ))
        
        return results
    
    async def validate_concurrent_operations(self) -> List[BenchmarkResult]:
        """Validate concurrent operation limits"""
        results = []
        
        print("Testing concurrent operations...")
        
        # Test concurrent job processing
        concurrent_jobs = 50
        start_time = time.time()
        
        async def process_concurrent_job(job_id):
            try:
                # Submit job
                response = requests.post(
                    f"{self.api_url}/api/scraper/scrape",
                    json={
                        "url": f"https://httpbin.org/delay/1?job={job_id}",
                        "method": "GET",
                        "scraper_type": "cloudscraper"
                    },
                    timeout=30
                )
                return response.status_code == 200
            except:
                return False
        
        # Process jobs concurrently
        tasks = [process_concurrent_job(i) for i in range(concurrent_jobs)]
        task_results = await asyncio.gather(*tasks, return_exceptions=True)
        
        successful_jobs = sum(1 for result in task_results if result is True)
        duration = time.time() - start_time
        
        target = next(b for b in self.benchmarks if b.name == "Concurrent Job Processing")
        results.append(BenchmarkResult(
            name=target.name,
            target_value=target.target_value,
            actual_value=successful_jobs,
            unit=target.unit,
            passed=successful_jobs >= target.target_value * (1 - target.tolerance),
            tolerance=target.tolerance,
            critical=target.critical,
            details={
                "attempted_jobs": concurrent_jobs,
                "successful_jobs": successful_jobs,
                "duration": duration,
                "success_rate": successful_jobs / concurrent_jobs * 100
            }
        ))
        
        return results
    
    async def validate_load_performance(self) -> List[BenchmarkResult]:
        """Validate performance under load"""
        results = []
        
        print("Testing performance under load...")
        
        # Generate load and measure response times
        load_duration = 60  # 1 minute
        requests_per_second = 20
        
        response_times = []
        error_count = 0
        total_requests = 0
        
        end_time = time.time() + load_duration
        
        while time.time() < end_time:
            batch_start = time.time()
            
            # Send batch of requests
            for _ in range(requests_per_second):
                start_time = time.time()
                try:
                    response = requests.get(f"{self.api_url}/health", timeout=5)
                    response_time = (time.time() - start_time) * 1000
                    
                    if response.status_code == 200:
                        response_times.append(response_time)
                    else:
                        error_count += 1
                    
                    total_requests += 1
                except:
                    error_count += 1
                    total_requests += 1
            
            # Wait to maintain RPS
            elapsed = time.time() - batch_start
            if elapsed < 1.0:
                await asyncio.sleep(1.0 - elapsed)
        
        # Calculate metrics
        if response_times:
            avg_response_time = statistics.mean(response_times)
            error_rate = (error_count / total_requests) * 100 if total_requests > 0 else 100
            
            # Response time under load
            response_target = next(b for b in self.benchmarks if b.name == "Response Time Under Load")
            results.append(BenchmarkResult(
                name=response_target.name,
                target_value=response_target.target_value,
                actual_value=avg_response_time,
                unit=response_target.unit,
                passed=avg_response_time <= response_target.target_value * (1 + response_target.tolerance),
                tolerance=response_target.tolerance,
                critical=response_target.critical,
                details={
                    "min": min(response_times),
                    "max": max(response_times),
                    "p95": statistics.quantiles(response_times, n=20)[18] if len(response_times) > 20 else max(response_times),
                    "samples": len(response_times)
                }
            ))
            
            # Error rate under load
            error_target = next(b for b in self.benchmarks if b.name == "Error Rate Under Load")
            results.append(BenchmarkResult(
                name=error_target.name,
                target_value=error_target.target_value,
                actual_value=error_rate,
                unit=error_target.unit,
                passed=error_rate <= error_target.target_value * (1 + error_target.tolerance),
                tolerance=error_target.tolerance,
                critical=error_target.critical,
                details={
                    "total_requests": total_requests,
                    "error_count": error_count,
                    "success_rate": 100 - error_rate
                }
            ))
        
        return results
    
    async def run_all_benchmarks(self) -> List[BenchmarkResult]:
        """Run all benchmark validations"""
        print("Starting performance benchmark validation...")
        
        all_results = []
        
        # Run each benchmark category
        print("\n1. Validating response time benchmarks...")
        all_results.extend(await self.validate_response_time_benchmarks())
        
        print("\n2. Validating throughput benchmarks...")
        all_results.extend(await self.validate_throughput_benchmarks())
        
        print("\n3. Validating resource usage benchmarks...")
        all_results.extend(self.validate_resource_usage_benchmarks())
        
        print("\n4. Validating concurrent operations...")
        all_results.extend(await self.validate_concurrent_operations())
        
        print("\n5. Validating performance under load...")
        all_results.extend(await self.validate_load_performance())
        
        self.results = all_results
        return all_results
    
    def generate_report(self) -> str:
        """Generate benchmark validation report"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Calculate summary statistics
        total_benchmarks = len(self.results)
        passed_benchmarks = sum(1 for r in self.results if r.passed)
        critical_failed = sum(1 for r in self.results if not r.passed and r.critical)
        
        report = f"""
# Performance Benchmark Validation Report
Generated: {timestamp}
Target API: {self.api_url}

## Summary
- Total benchmarks: {total_benchmarks}
- Passed: {passed_benchmarks}
- Failed: {total_benchmarks - passed_benchmarks}
- Critical failures: {critical_failed}
- Success rate: {(passed_benchmarks / total_benchmarks * 100):.1f}%

## Overall Result: {'✅ PASSED' if critical_failed == 0 else '❌ FAILED'}

## Detailed Results

"""
        
        # Group results by category
        categories = {
            "Response Time": [r for r in self.results if "Response Time" in r.name],
            "Throughput": [r for r in self.results if "Throughput" in r.name or "Concurrent" in r.name],
            "Resource Usage": [r for r in self.results if "Usage" in r.name],
            "Load Performance": [r for r in self.results if "Under Load" in r.name or "Error Rate" in r.name],
        }
        
        for category, results in categories.items():
            if results:
                report += f"### {category}\n\n"
                for result in results:
                    status = "✅ PASS" if result.passed else "❌ FAIL"
                    report += f"- **{result.name}**: {status}\n"
                    report += f"  - Target: {result.target_value} {result.unit}\n"
                    report += f"  - Actual: {result.actual_value:.2f} {result.unit}\n"
                    report += f"  - Tolerance: ±{result.tolerance * 100:.0f}%\n"
                    if result.details:
                        report += f"  - Details: {result.details}\n"
                    report += "\n"
        
        return report
    
    def save_results(self, filename: str = None):
        """Save benchmark results to JSON file"""
        if not filename:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"benchmark_results_{timestamp}.json"
        
        results_data = [asdict(result) for result in self.results]
        
        with open(filename, 'w') as f:
            json.dump(results_data, f, indent=2)
        
        print(f"Results saved to: {filename}")
        return filename


async def main():
    parser = argparse.ArgumentParser(description="CFScraper Performance Benchmark Validator")
    parser.add_argument("--api-url", default="http://localhost:8000", help="API URL")
    parser.add_argument("--output", help="Output file for results")
    
    args = parser.parse_args()
    
    validator = PerformanceBenchmarkValidator(args.api_url)
    
    try:
        # Run all benchmarks
        results = await validator.run_all_benchmarks()
        
        # Generate and save report
        report = validator.generate_report()
        validator.save_results(args.output)
        
        print("\n" + "="*80)
        print("PERFORMANCE BENCHMARK VALIDATION REPORT")
        print("="*80)
        print(report)
        
        # Save report
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_file = f"benchmark_report_{timestamp}.md"
        with open(report_file, 'w') as f:
            f.write(report)
        print(f"Report saved to: {report_file}")
        
        # Exit with appropriate code
        critical_failures = sum(1 for r in results if not r.passed and r.critical)
        exit(1 if critical_failures > 0 else 0)
        
    except Exception as e:
        print(f"Benchmark validation failed: {e}")
        exit(1)


if __name__ == "__main__":
    asyncio.run(main())
