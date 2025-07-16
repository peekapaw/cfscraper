#!/usr/bin/env python3
"""
Load test runner script for CFScraper API.

This script provides automated load testing with:
- Multiple test scenarios
- Performance metrics collection
- Result analysis and reporting
- HTML report generation
"""

import os
import sys
import time
import json
import argparse
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List

import requests


class LoadTestRunner:
    """Load test runner with automated scenarios"""
    
    def __init__(self, host: str = "http://localhost:8000"):
        self.host = host
        self.results_dir = Path("load_test_results")
        self.results_dir.mkdir(exist_ok=True)
        
    def check_api_health(self) -> bool:
        """Check if API is healthy before running tests"""
        try:
            response = requests.get(f"{self.host}/health", timeout=10)
            return response.status_code == 200
        except Exception as e:
            print(f"API health check failed: {e}")
            return False
    
    def run_test_scenario(
        self, 
        scenario: str,
        users: int = 10,
        spawn_rate: int = 2,
        duration: str = "5m",
        additional_args: List[str] = None
    ) -> Dict[str, Any]:
        """Run a specific load test scenario"""
        
        if not self.check_api_health():
            raise Exception("API is not healthy, cannot run load tests")
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        test_name = f"{scenario}_{timestamp}"
        
        # Prepare output files
        csv_file = self.results_dir / f"{test_name}_stats.csv"
        html_file = self.results_dir / f"{test_name}_report.html"
        
        # Build locust command
        cmd = [
            "locust",
            "-f", "locustfile.py",
            "--host", self.host,
            "--users", str(users),
            "--spawn-rate", str(spawn_rate),
            "--run-time", duration,
            "--headless",
            "--csv", str(csv_file.with_suffix("")),
            "--html", str(html_file),
            "--loglevel", "INFO"
        ]
        
        # Add scenario-specific arguments
        if scenario == "step":
            cmd.extend(["StepLoadShape"])
        elif scenario == "spike":
            cmd.extend(["SpikeLoadShape"])
        elif scenario == "stress":
            cmd.extend(["StressLoadShape"])
        
        if additional_args:
            cmd.extend(additional_args)
        
        print(f"Running {scenario} load test...")
        print(f"Command: {' '.join(cmd)}")
        
        # Run the test
        start_time = time.time()
        try:
            result = subprocess.run(
                cmd,
                cwd="load_tests",
                capture_output=True,
                text=True,
                timeout=600  # 10 minutes timeout
            )
            
            end_time = time.time()
            duration_seconds = end_time - start_time
            
            # Parse results
            test_result = {
                "scenario": scenario,
                "timestamp": timestamp,
                "duration_seconds": duration_seconds,
                "command": " ".join(cmd),
                "return_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "csv_file": str(csv_file),
                "html_file": str(html_file),
                "success": result.returncode == 0
            }
            
            # Try to parse CSV stats if available
            if csv_file.exists():
                test_result["stats"] = self.parse_csv_stats(csv_file)
            
            return test_result
            
        except subprocess.TimeoutExpired:
            return {
                "scenario": scenario,
                "timestamp": timestamp,
                "error": "Test timed out after 10 minutes",
                "success": False
            }
        except Exception as e:
            return {
                "scenario": scenario,
                "timestamp": timestamp,
                "error": str(e),
                "success": False
            }
    
    def parse_csv_stats(self, csv_file: Path) -> Dict[str, Any]:
        """Parse Locust CSV statistics"""
        try:
            import pandas as pd
            
            # Read the stats CSV
            df = pd.read_csv(csv_file)
            
            if df.empty:
                return {}
            
            # Calculate summary statistics
            total_requests = df['Request Count'].sum()
            total_failures = df['Failure Count'].sum()
            avg_response_time = df['Average Response Time'].mean()
            max_response_time = df['Max Response Time'].max()
            
            return {
                "total_requests": int(total_requests),
                "total_failures": int(total_failures),
                "failure_rate": (total_failures / total_requests * 100) if total_requests > 0 else 0,
                "avg_response_time": float(avg_response_time),
                "max_response_time": float(max_response_time),
                "requests_per_second": df['Requests/s'].mean() if 'Requests/s' in df.columns else 0
            }
            
        except Exception as e:
            print(f"Error parsing CSV stats: {e}")
            return {}
    
    def run_all_scenarios(self) -> List[Dict[str, Any]]:
        """Run all predefined test scenarios"""
        scenarios = [
            {
                "name": "baseline",
                "users": 5,
                "spawn_rate": 1,
                "duration": "2m",
                "description": "Baseline test with minimal load"
            },
            {
                "name": "normal",
                "users": 20,
                "spawn_rate": 5,
                "duration": "5m",
                "description": "Normal load test"
            },
            {
                "name": "step",
                "users": 50,
                "spawn_rate": 5,
                "duration": "5m",
                "description": "Step load pattern"
            },
            {
                "name": "spike",
                "users": 100,
                "spawn_rate": 10,
                "duration": "5m",
                "description": "Spike load pattern"
            },
            {
                "name": "stress",
                "users": 100,
                "spawn_rate": 10,
                "duration": "10m",
                "description": "Stress test with high sustained load"
            }
        ]
        
        results = []
        
        for scenario in scenarios:
            print(f"\n{'='*60}")
            print(f"Running scenario: {scenario['name']}")
            print(f"Description: {scenario['description']}")
            print(f"Users: {scenario['users']}, Spawn rate: {scenario['spawn_rate']}, Duration: {scenario['duration']}")
            print(f"{'='*60}")
            
            try:
                result = self.run_test_scenario(
                    scenario["name"],
                    scenario["users"],
                    scenario["spawn_rate"],
                    scenario["duration"]
                )
                result["description"] = scenario["description"]
                results.append(result)
                
                if result["success"]:
                    print(f"✅ {scenario['name']} completed successfully")
                    if "stats" in result:
                        stats = result["stats"]
                        print(f"   Total requests: {stats.get('total_requests', 'N/A')}")
                        print(f"   Failure rate: {stats.get('failure_rate', 'N/A'):.2f}%")
                        print(f"   Avg response time: {stats.get('avg_response_time', 'N/A'):.2f}ms")
                else:
                    print(f"❌ {scenario['name']} failed")
                    if "error" in result:
                        print(f"   Error: {result['error']}")
                
                # Wait between scenarios
                if scenario != scenarios[-1]:  # Don't wait after last scenario
                    print("Waiting 30 seconds before next scenario...")
                    time.sleep(30)
                    
            except Exception as e:
                print(f"❌ {scenario['name']} failed with exception: {e}")
                results.append({
                    "scenario": scenario["name"],
                    "description": scenario["description"],
                    "error": str(e),
                    "success": False
                })
        
        return results
    
    def generate_summary_report(self, results: List[Dict[str, Any]]) -> str:
        """Generate a summary report of all test results"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        report = f"""
# Load Test Summary Report
Generated: {timestamp}
Target Host: {self.host}

## Test Results Overview

"""
        
        successful_tests = [r for r in results if r.get("success", False)]
        failed_tests = [r for r in results if not r.get("success", False)]
        
        report += f"- Total scenarios: {len(results)}\n"
        report += f"- Successful: {len(successful_tests)}\n"
        report += f"- Failed: {len(failed_tests)}\n\n"
        
        if successful_tests:
            report += "## Successful Tests\n\n"
            for result in successful_tests:
                report += f"### {result['scenario'].title()}\n"
                report += f"- Description: {result.get('description', 'N/A')}\n"
                report += f"- Duration: {result.get('duration_seconds', 'N/A'):.1f} seconds\n"
                
                if "stats" in result:
                    stats = result["stats"]
                    report += f"- Total requests: {stats.get('total_requests', 'N/A')}\n"
                    report += f"- Failure rate: {stats.get('failure_rate', 'N/A'):.2f}%\n"
                    report += f"- Avg response time: {stats.get('avg_response_time', 'N/A'):.2f}ms\n"
                    report += f"- Max response time: {stats.get('max_response_time', 'N/A'):.2f}ms\n"
                    report += f"- Requests/sec: {stats.get('requests_per_second', 'N/A'):.2f}\n"
                
                if "html_file" in result:
                    report += f"- HTML Report: {result['html_file']}\n"
                
                report += "\n"
        
        if failed_tests:
            report += "## Failed Tests\n\n"
            for result in failed_tests:
                report += f"### {result['scenario'].title()}\n"
                report += f"- Error: {result.get('error', 'Unknown error')}\n\n"
        
        # Save summary report
        summary_file = self.results_dir / f"summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        with open(summary_file, 'w') as f:
            f.write(report)
        
        print(f"\nSummary report saved to: {summary_file}")
        return report


def main():
    parser = argparse.ArgumentParser(description="CFScraper Load Test Runner")
    parser.add_argument("--host", default="http://localhost:8000", help="Target host URL")
    parser.add_argument("--scenario", choices=["baseline", "normal", "step", "spike", "stress", "all"], 
                       default="all", help="Test scenario to run")
    parser.add_argument("--users", type=int, default=10, help="Number of users")
    parser.add_argument("--spawn-rate", type=int, default=2, help="User spawn rate")
    parser.add_argument("--duration", default="5m", help="Test duration")
    
    args = parser.parse_args()
    
    runner = LoadTestRunner(args.host)
    
    if args.scenario == "all":
        print("Running all test scenarios...")
        results = runner.run_all_scenarios()
        summary = runner.generate_summary_report(results)
        print("\n" + "="*60)
        print("FINAL SUMMARY")
        print("="*60)
        print(summary)
    else:
        print(f"Running {args.scenario} scenario...")
        result = runner.run_test_scenario(
            args.scenario,
            args.users,
            args.spawn_rate,
            args.duration
        )
        
        if result["success"]:
            print("✅ Test completed successfully")
            if "stats" in result:
                stats = result["stats"]
                print(f"Total requests: {stats.get('total_requests', 'N/A')}")
                print(f"Failure rate: {stats.get('failure_rate', 'N/A'):.2f}%")
                print(f"Avg response time: {stats.get('avg_response_time', 'N/A'):.2f}ms")
        else:
            print("❌ Test failed")
            if "error" in result:
                print(f"Error: {result['error']}")


if __name__ == "__main__":
    main()
