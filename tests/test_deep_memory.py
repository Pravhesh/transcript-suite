"""
Unit tests for Deep Memory usage trace and process inspection.
"""

from transcript_suite.asr.memory import VRAMManager, get_deep_memory_trace


def test_deep_memory_trace_structure():
    vm = VRAMManager()
    trace = vm.get_deep_memory_trace()
    
    assert "process" in trace
    assert "system_ram" in trace
    assert "top_processes" in trace
    assert "gpu" in trace

    # Process stats
    proc = trace["process"]
    assert "pid" in proc
    assert "rss_mb" in proc
    assert "rss_gb" in proc
    assert "vms_mb" in proc
    assert proc["rss_mb"] > 0

    # System RAM stats
    sys = trace["system_ram"]
    assert "total_gb" in sys
    assert "used_gb" in sys
    assert "free_gb" in sys
    assert sys["total_gb"] > 0

    # Top processes
    top = trace["top_processes"]
    assert isinstance(top, list)
    assert len(top) <= 10
    if top:
        p0 = top[0]
        assert "pid" in p0
        assert "name" in p0
        assert "user" in p0
        assert "rss_mb" in p0
        assert "rss_gb" in p0
        assert "percent" in p0
        # Should be sorted descending by rss_mb
        for i in range(len(top) - 1):
            assert top[i]["rss_mb"] >= top[i + 1]["rss_mb"]

    # GPU stats
    gpu = trace["gpu"]
    assert "available" in gpu
    assert "total_gb" in gpu
    assert "allocated_gb" in gpu
    assert "reserved_gb" in gpu
    assert "free_gb" in gpu


def test_top_level_helper():
    trace = get_deep_memory_trace()
    assert isinstance(trace, dict)
    assert "top_processes" in trace
