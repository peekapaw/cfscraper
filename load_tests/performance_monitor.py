#!/usr/bin/env python3
"""
Performance monitoring script for CFScraper during load tests.

This script provides:
- Real-time performance monitoring
- System resource tracking
- Database performance metrics
- Redis performance metrics
- Custom application metrics collection
"""

import time
import json
import psutil
import requests
import threading
from datetime import datetime
from typing import Dict, Any, List
from dataclasses import dataclass, asdict
import argparse


@dataclass
class PerformanceMetrics:
    """Performance metrics data structure"""
    timestamp: str
    cpu_percent: float
    memory_percent: float
    memory_used_mb: float
    memory_available_mb: float
    disk_io_read_mb: float
    disk_io_write_mb: float
    network_sent_mb: float
    network_recv_mb: float
    active_connections: int
    response_time_ms: float
    api_requests_per_second: float
    database_connections: int
    redis_connections: int
    queue_size: int
    error_rate: float


class PerformanceMonitor:
    """Real-time performance monitoring"""
    
    def __init__(self, api_host: str = "http://localhost:8000", interval: int = 5):
        self.api_host = api_host
        self.interval = interval
        self.monitoring = False
        self.metrics_history: List[PerformanceMetrics] = []
        self.last_disk_io = None
        self.last_network_io = None
        
    def get_system_metrics(self) -> Dict[str, Any]:
        """Get system performance metrics"""
        # CPU and Memory
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        
        # Disk I/O
        disk_io = psutil.disk_io_counters()
        disk_read_mb = 0
        disk_write_mb = 0
        
        if self.last_disk_io and disk_io:
            disk_read_mb = (disk_io.read_bytes - self.last_disk_io.read_bytes) / 1024 / 1024
            disk_write_mb = (disk_io.write_bytes - self.last_disk_io.write_bytes) / 1024 / 1024
        
        self.last_disk_io = disk_io
        
        # Network I/O
        network_io = psutil.net_io_counters()
        network_sent_mb = 0
        network_recv_mb = 0
        
        if self.last_network_io and network_io:
            network_sent_mb = (network_io.bytes_sent - self.last_network_io.bytes_sent) / 1024 / 1024
            network_recv_mb = (network_io.bytes_recv - self.last_network_io.bytes_recv) / 1024 / 1024
        
        self.last_network_io = network_io
        
        # Network connections
        connections = psutil.net_connections()
        active_connections = len([c for c in connections if c.status == 'ESTABLISHED'])
        
        return {
            "cpu_percent": cpu_percent,
            "memory_percent": memory.percent,
            "memory_used_mb": memory.used / 1024 / 1024,
            "memory_available_mb": memory.available / 1024 / 1024,
            "disk_io_read_mb": disk_read_mb,
            "disk_io_write_mb": disk_write_mb,
            "network_sent_mb": network_sent_mb,
            "network_recv_mb": network_recv_mb,
            "active_connections": active_connections
        }
    
    def get_api_metrics(self) -> Dict[str, Any]:
        """Get API performance metrics"""
        try:
            # Health check with timing
            start_time = time.time()
            response = requests.get(f"{self.api_host}/health", timeout=10)
            response_time_ms = (time.time() - start_time) * 1000
            
            api_healthy = response.status_code == 200
            
            # Try to get application metrics
            app_metrics = {}
            try:
                metrics_response = requests.get(f"{self.api_host}/metrics", timeout=5)
                if metrics_response.status_code == 200:
                    # Parse Prometheus metrics (simplified)
                    metrics_text = metrics_response.text
                    app_metrics = self.parse_prometheus_metrics(metrics_text)
            except:
                pass
            
            return {
                "response_time_ms": response_time_ms,
                "api_healthy": api_healthy,
                "api_requests_per_second": app_metrics.get("http_requests_per_second", 0),
                "database_connections": app_metrics.get("db_connections", 0),
                "redis_connections": app_metrics.get("redis_connections", 0),
                "queue_size": app_metrics.get("job_queue_size", 0),
                "error_rate": app_metrics.get("error_rate", 0)
            }
            
        except Exception as e:
            print(f"Error getting API metrics: {e}")
            return {
                "response_time_ms": 0,
                "api_healthy": False,
                "api_requests_per_second": 0,
                "database_connections": 0,
                "redis_connections": 0,
                "queue_size": 0,
                "error_rate": 100
            }
    
    def parse_prometheus_metrics(self, metrics_text: str) -> Dict[str, float]:
        """Parse Prometheus metrics (simplified parser)"""
        metrics = {}
        
        for line in metrics_text.split('\n'):
            if line.startswith('#') or not line.strip():
                continue
            
            try:
                if ' ' in line:
                    metric_name, value = line.split(' ', 1)
                    
                    # Extract metric name without labels
                    if '{' in metric_name:
                        metric_name = metric_name.split('{')[0]
                    
                    # Map specific metrics we care about
                    if 'http_requests_total' in metric_name:
                        metrics['http_requests_per_second'] = float(value)
                    elif 'db_connection_pool_size' in metric_name:
                        metrics['db_connections'] = float(value)
                    elif 'redis_connection_pool_size' in metric_name:
                        metrics['redis_connections'] = float(value)
                    elif 'job_queue_size' in metric_name:
                        metrics['job_queue_size'] = float(value)
                    elif 'error_rate' in metric_name:
                        metrics['error_rate'] = float(value)
                        
            except ValueError:
                continue
        
        return metrics
    
    def collect_metrics(self) -> PerformanceMetrics:
        """Collect all performance metrics"""
        timestamp = datetime.now().isoformat()
        
        # Get system metrics
        system_metrics = self.get_system_metrics()
        
        # Get API metrics
        api_metrics = self.get_api_metrics()
        
        # Combine all metrics
        return PerformanceMetrics(
            timestamp=timestamp,
            cpu_percent=system_metrics["cpu_percent"],
            memory_percent=system_metrics["memory_percent"],
            memory_used_mb=system_metrics["memory_used_mb"],
            memory_available_mb=system_metrics["memory_available_mb"],
            disk_io_read_mb=system_metrics["disk_io_read_mb"],
            disk_io_write_mb=system_metrics["disk_io_write_mb"],
            network_sent_mb=system_metrics["network_sent_mb"],
            network_recv_mb=system_metrics["network_recv_mb"],
            active_connections=system_metrics["active_connections"],
            response_time_ms=api_metrics["response_time_ms"],
            api_requests_per_second=api_metrics["api_requests_per_second"],
            database_connections=api_metrics["database_connections"],
            redis_connections=api_metrics["redis_connections"],
            queue_size=api_metrics["queue_size"],
            error_rate=api_metrics["error_rate"]
        )
    
    def start_monitoring(self):
        """Start performance monitoring"""
        self.monitoring = True
        print(f"Starting performance monitoring (interval: {self.interval}s)")
        print(f"Target API: {self.api_host}")
        print("-" * 80)
        
        while self.monitoring:
            try:
                metrics = self.collect_metrics()
                self.metrics_history.append(metrics)
                
                # Print real-time metrics
                self.print_metrics(metrics)
                
                # Keep only last 1000 metrics to prevent memory issues
                if len(self.metrics_history) > 1000:
                    self.metrics_history = self.metrics_history[-1000:]
                
                time.sleep(self.interval)
                
            except KeyboardInterrupt:
                print("\nStopping monitoring...")
                break
            except Exception as e:
                print(f"Error during monitoring: {e}")
                time.sleep(self.interval)
    
    def print_metrics(self, metrics: PerformanceMetrics):
        """Print metrics in a readable format"""
        print(f"\r{metrics.timestamp} | "
              f"CPU: {metrics.cpu_percent:5.1f}% | "
              f"MEM: {metrics.memory_percent:5.1f}% | "
              f"API: {metrics.response_time_ms:6.1f}ms | "
              f"RPS: {metrics.api_requests_per_second:5.1f} | "
              f"Queue: {metrics.queue_size:3.0f} | "
              f"Errors: {metrics.error_rate:5.1f}%", end="")
    
    def stop_monitoring(self):
        """Stop performance monitoring"""
        self.monitoring = False
    
    def save_metrics(self, filename: str = None):
        """Save collected metrics to JSON file"""
        if not filename:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"performance_metrics_{timestamp}.json"
        
        metrics_data = [asdict(m) for m in self.metrics_history]
        
        with open(filename, 'w') as f:
            json.dump(metrics_data, f, indent=2)
        
        print(f"\nMetrics saved to: {filename}")
        return filename
    
    def generate_summary(self) -> Dict[str, Any]:
        """Generate performance summary"""
        if not self.metrics_history:
            return {}
        
        # Calculate averages and peaks
        cpu_values = [m.cpu_percent for m in self.metrics_history]
        memory_values = [m.memory_percent for m in self.metrics_history]
        response_times = [m.response_time_ms for m in self.metrics_history if m.response_time_ms > 0]
        rps_values = [m.api_requests_per_second for m in self.metrics_history]
        
        summary = {
            "monitoring_duration_minutes": len(self.metrics_history) * self.interval / 60,
            "total_samples": len(self.metrics_history),
            "cpu": {
                "avg": sum(cpu_values) / len(cpu_values) if cpu_values else 0,
                "max": max(cpu_values) if cpu_values else 0,
                "min": min(cpu_values) if cpu_values else 0
            },
            "memory": {
                "avg": sum(memory_values) / len(memory_values) if memory_values else 0,
                "max": max(memory_values) if memory_values else 0,
                "min": min(memory_values) if memory_values else 0
            },
            "response_time": {
                "avg": sum(response_times) / len(response_times) if response_times else 0,
                "max": max(response_times) if response_times else 0,
                "min": min(response_times) if response_times else 0
            },
            "requests_per_second": {
                "avg": sum(rps_values) / len(rps_values) if rps_values else 0,
                "max": max(rps_values) if rps_values else 0
            }
        }
        
        return summary


def main():
    parser = argparse.ArgumentParser(description="CFScraper Performance Monitor")
    parser.add_argument("--host", default="http://localhost:8000", help="API host URL")
    parser.add_argument("--interval", type=int, default=5, help="Monitoring interval in seconds")
    parser.add_argument("--duration", type=int, help="Monitoring duration in minutes")
    parser.add_argument("--output", help="Output file for metrics")
    
    args = parser.parse_args()
    
    monitor = PerformanceMonitor(args.host, args.interval)
    
    try:
        if args.duration:
            # Run for specified duration
            def stop_after_duration():
                time.sleep(args.duration * 60)
                monitor.stop_monitoring()
            
            timer = threading.Timer(args.duration * 60, stop_after_duration)
            timer.start()
        
        monitor.start_monitoring()
        
    except KeyboardInterrupt:
        print("\nMonitoring stopped by user")
    
    finally:
        # Save metrics and generate summary
        filename = monitor.save_metrics(args.output)
        summary = monitor.generate_summary()
        
        print("\n" + "="*60)
        print("PERFORMANCE SUMMARY")
        print("="*60)
        print(f"Monitoring duration: {summary.get('monitoring_duration_minutes', 0):.1f} minutes")
        print(f"Total samples: {summary.get('total_samples', 0)}")
        
        if 'cpu' in summary:
            cpu = summary['cpu']
            print(f"CPU - Avg: {cpu['avg']:.1f}%, Max: {cpu['max']:.1f}%, Min: {cpu['min']:.1f}%")
        
        if 'memory' in summary:
            mem = summary['memory']
            print(f"Memory - Avg: {mem['avg']:.1f}%, Max: {mem['max']:.1f}%, Min: {mem['min']:.1f}%")
        
        if 'response_time' in summary:
            rt = summary['response_time']
            print(f"Response Time - Avg: {rt['avg']:.1f}ms, Max: {rt['max']:.1f}ms, Min: {rt['min']:.1f}ms")
        
        if 'requests_per_second' in summary:
            rps = summary['requests_per_second']
            print(f"Requests/sec - Avg: {rps['avg']:.1f}, Max: {rps['max']:.1f}")


if __name__ == "__main__":
    main()
