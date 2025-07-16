#!/usr/bin/env python3
"""
Auto-scaling test script for CFScraper.

This script tests the auto-scaling functionality by:
- Monitoring current pod count
- Generating load to trigger scaling
- Validating scaling behavior
- Testing scale-down after load reduction
"""

import time
import json
import requests
import subprocess
import threading
from typing import Dict, Any, List
from datetime import datetime
import argparse


class AutoScalingTester:
    """Auto-scaling functionality tester"""
    
    def __init__(self, namespace: str = "default", api_url: str = "http://localhost:8000"):
        self.namespace = namespace
        self.api_url = api_url
        self.monitoring = False
        self.metrics_history = []
        
    def get_pod_count(self, deployment: str) -> int:
        """Get current pod count for deployment"""
        try:
            result = subprocess.run([
                "kubectl", "get", "deployment", deployment,
                "-n", self.namespace,
                "-o", "jsonpath={.status.replicas}"
            ], capture_output=True, text=True, check=True)
            
            return int(result.stdout.strip()) if result.stdout.strip() else 0
        except (subprocess.CalledProcessError, ValueError):
            return 0
    
    def get_hpa_status(self, hpa_name: str) -> Dict[str, Any]:
        """Get HPA status"""
        try:
            result = subprocess.run([
                "kubectl", "get", "hpa", hpa_name,
                "-n", self.namespace,
                "-o", "json"
            ], capture_output=True, text=True, check=True)
            
            return json.loads(result.stdout)
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            return {}
    
    def get_pod_metrics(self, deployment: str) -> Dict[str, Any]:
        """Get pod resource metrics"""
        try:
            result = subprocess.run([
                "kubectl", "top", "pods",
                "-n", self.namespace,
                "-l", f"app={deployment}",
                "--no-headers"
            ], capture_output=True, text=True, check=True)
            
            pods_data = []
            for line in result.stdout.strip().split('\n'):
                if line:
                    parts = line.split()
                    if len(parts) >= 3:
                        pods_data.append({
                            'name': parts[0],
                            'cpu': parts[1],
                            'memory': parts[2]
                        })
            
            return {'pods': pods_data}
        except subprocess.CalledProcessError:
            return {'pods': []}
    
    def generate_load(self, duration: int = 300, requests_per_second: int = 10):
        """Generate load to trigger auto-scaling"""
        print(f"Generating load: {requests_per_second} RPS for {duration} seconds")
        
        end_time = time.time() + duration
        request_count = 0
        error_count = 0
        
        while time.time() < end_time and self.monitoring:
            start_time = time.time()
            
            # Make multiple requests to simulate load
            for _ in range(requests_per_second):
                try:
                    # Submit scraping jobs
                    response = requests.post(
                        f"{self.api_url}/api/scraper/scrape",
                        json={
                            "url": "https://httpbin.org/get",
                            "method": "GET",
                            "scraper_type": "cloudscraper"
                        },
                        timeout=5
                    )
                    
                    if response.status_code == 200:
                        request_count += 1
                    else:
                        error_count += 1
                        
                except requests.RequestException:
                    error_count += 1
            
            # Wait to maintain RPS
            elapsed = time.time() - start_time
            if elapsed < 1.0:
                time.sleep(1.0 - elapsed)
        
        print(f"Load generation completed. Requests: {request_count}, Errors: {error_count}")
        return request_count, error_count
    
    def monitor_scaling(self, deployment: str, hpa_name: str, duration: int = 600):
        """Monitor scaling behavior"""
        print(f"Monitoring scaling for {duration} seconds...")
        
        start_time = time.time()
        end_time = start_time + duration
        
        while time.time() < end_time and self.monitoring:
            timestamp = datetime.now().isoformat()
            
            # Get current metrics
            pod_count = self.get_pod_count(deployment)
            hpa_status = self.get_hpa_status(hpa_name)
            pod_metrics = self.get_pod_metrics(deployment)
            
            # Extract HPA metrics
            current_replicas = hpa_status.get('status', {}).get('currentReplicas', 0)
            desired_replicas = hpa_status.get('status', {}).get('desiredReplicas', 0)
            current_metrics = hpa_status.get('status', {}).get('currentMetrics', [])
            
            metrics_data = {
                'timestamp': timestamp,
                'pod_count': pod_count,
                'current_replicas': current_replicas,
                'desired_replicas': desired_replicas,
                'current_metrics': current_metrics,
                'pod_metrics': pod_metrics
            }
            
            self.metrics_history.append(metrics_data)
            
            # Print current status
            print(f"\r{timestamp} | Pods: {pod_count} | Current: {current_replicas} | Desired: {desired_replicas}", end="")
            
            time.sleep(10)  # Check every 10 seconds
        
        print("\nMonitoring completed")
    
    def test_scale_up(self, deployment: str = "cfscraper-api", hpa_name: str = "cfscraper-api-hpa"):
        """Test scale-up behavior"""
        print("="*60)
        print("TESTING SCALE-UP BEHAVIOR")
        print("="*60)
        
        # Get initial state
        initial_pods = self.get_pod_count(deployment)
        print(f"Initial pod count: {initial_pods}")
        
        # Start monitoring in background
        self.monitoring = True
        monitor_thread = threading.Thread(
            target=self.monitor_scaling,
            args=(deployment, hpa_name, 600)  # Monitor for 10 minutes
        )
        monitor_thread.start()
        
        # Wait a bit to establish baseline
        time.sleep(30)
        
        # Generate load to trigger scaling
        load_thread = threading.Thread(
            target=self.generate_load,
            args=(300, 20)  # 5 minutes at 20 RPS
        )
        load_thread.start()
        
        # Wait for load generation to complete
        load_thread.join()
        
        # Continue monitoring for scale-down
        print("\nWaiting for scale-down...")
        time.sleep(300)  # Wait 5 more minutes
        
        # Stop monitoring
        self.monitoring = False
        monitor_thread.join()
        
        # Get final state
        final_pods = self.get_pod_count(deployment)
        print(f"Final pod count: {final_pods}")
        
        return self.analyze_scaling_behavior(initial_pods, final_pods)
    
    def test_scale_down(self, deployment: str = "cfscraper-api"):
        """Test scale-down behavior after load reduction"""
        print("="*60)
        print("TESTING SCALE-DOWN BEHAVIOR")
        print("="*60)
        
        current_pods = self.get_pod_count(deployment)
        print(f"Current pod count: {current_pods}")
        
        if current_pods <= 2:
            print("Not enough pods to test scale-down. Run scale-up test first.")
            return False
        
        # Monitor scale-down for 10 minutes
        self.monitoring = True
        monitor_thread = threading.Thread(
            target=self.monitor_scaling,
            args=(deployment, "cfscraper-api-hpa", 600)
        )
        monitor_thread.start()
        
        # Wait for scale-down
        monitor_thread.join()
        
        final_pods = self.get_pod_count(deployment)
        print(f"Final pod count after scale-down: {final_pods}")
        
        return final_pods < current_pods
    
    def analyze_scaling_behavior(self, initial_pods: int, final_pods: int) -> Dict[str, Any]:
        """Analyze scaling behavior from collected metrics"""
        if not self.metrics_history:
            return {"error": "No metrics collected"}
        
        # Find maximum pod count during test
        max_pods = max(m['pod_count'] for m in self.metrics_history)
        
        # Find scaling events
        scaling_events = []
        prev_pods = initial_pods
        
        for metric in self.metrics_history:
            current_pods = metric['pod_count']
            if current_pods != prev_pods:
                scaling_events.append({
                    'timestamp': metric['timestamp'],
                    'from': prev_pods,
                    'to': current_pods,
                    'direction': 'up' if current_pods > prev_pods else 'down'
                })
                prev_pods = current_pods
        
        # Calculate scaling metrics
        scale_up_events = [e for e in scaling_events if e['direction'] == 'up']
        scale_down_events = [e for e in scaling_events if e['direction'] == 'down']
        
        analysis = {
            'initial_pods': initial_pods,
            'max_pods': max_pods,
            'final_pods': final_pods,
            'scaling_events': scaling_events,
            'scale_up_events': len(scale_up_events),
            'scale_down_events': len(scale_down_events),
            'scaling_factor': max_pods / initial_pods if initial_pods > 0 else 0,
            'test_duration_minutes': len(self.metrics_history) * 10 / 60,  # 10 second intervals
        }
        
        # Determine if scaling worked as expected
        analysis['scale_up_successful'] = max_pods > initial_pods
        analysis['scale_down_successful'] = final_pods < max_pods
        analysis['overall_success'] = analysis['scale_up_successful'] and analysis['scale_down_successful']
        
        return analysis
    
    def save_metrics(self, filename: str = None):
        """Save collected metrics to file"""
        if not filename:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"autoscaling_metrics_{timestamp}.json"
        
        with open(filename, 'w') as f:
            json.dump(self.metrics_history, f, indent=2)
        
        print(f"Metrics saved to: {filename}")
        return filename
    
    def generate_report(self, analysis: Dict[str, Any]) -> str:
        """Generate auto-scaling test report"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        report = f"""
# Auto-scaling Test Report
Generated: {timestamp}

## Test Summary
- Initial pods: {analysis.get('initial_pods', 'N/A')}
- Maximum pods: {analysis.get('max_pods', 'N/A')}
- Final pods: {analysis.get('final_pods', 'N/A')}
- Scaling factor: {analysis.get('scaling_factor', 'N/A'):.2f}x
- Test duration: {analysis.get('test_duration_minutes', 'N/A'):.1f} minutes

## Scaling Events
- Scale-up events: {analysis.get('scale_up_events', 'N/A')}
- Scale-down events: {analysis.get('scale_down_events', 'N/A')}

## Results
- Scale-up successful: {'✅' if analysis.get('scale_up_successful') else '❌'}
- Scale-down successful: {'✅' if analysis.get('scale_down_successful') else '❌'}
- Overall success: {'✅' if analysis.get('overall_success') else '❌'}

## Scaling Events Timeline
"""
        
        for event in analysis.get('scaling_events', []):
            report += f"- {event['timestamp']}: {event['from']} → {event['to']} pods ({event['direction']})\n"
        
        return report


def main():
    parser = argparse.ArgumentParser(description="CFScraper Auto-scaling Tester")
    parser.add_argument("--namespace", default="default", help="Kubernetes namespace")
    parser.add_argument("--api-url", default="http://localhost:8000", help="API URL")
    parser.add_argument("--deployment", default="cfscraper-api", help="Deployment name")
    parser.add_argument("--hpa", default="cfscraper-api-hpa", help="HPA name")
    parser.add_argument("--test", choices=["scale-up", "scale-down", "full"], 
                       default="full", help="Test type")
    
    args = parser.parse_args()
    
    tester = AutoScalingTester(args.namespace, args.api_url)
    
    try:
        if args.test in ["scale-up", "full"]:
            analysis = tester.test_scale_up(args.deployment, args.hpa)
            
            # Save metrics and generate report
            tester.save_metrics()
            report = tester.generate_report(analysis)
            
            print("\n" + "="*60)
            print("AUTO-SCALING TEST REPORT")
            print("="*60)
            print(report)
            
            # Save report
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            report_file = f"autoscaling_report_{timestamp}.md"
            with open(report_file, 'w') as f:
                f.write(report)
            print(f"\nReport saved to: {report_file}")
        
        if args.test in ["scale-down", "full"]:
            if args.test == "scale-down":
                success = tester.test_scale_down(args.deployment)
                print(f"Scale-down test: {'✅ PASSED' if success else '❌ FAILED'}")
    
    except KeyboardInterrupt:
        print("\nTest interrupted by user")
        tester.monitoring = False
    except Exception as e:
        print(f"Test failed with error: {e}")


if __name__ == "__main__":
    main()
