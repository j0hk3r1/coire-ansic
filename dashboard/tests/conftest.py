"""Shared fixtures. Stub bifrost_get + filesystem reads so unit tests run offline."""
import json
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def fake_logs():
    """Sample bifrost /api/logs payload."""
    return {
        "logs": [
            {
                "timestamp": "2026-05-08T12:00:00.000Z",
                "routing_rule_name": "best",
                "provider": "groq",
                "model": "llama-3.3-70b",
                "status": "success",
                "latency": 1500,
                "fallback_index": 0,
                "stream": False,
            },
            {
                "timestamp": "2026-05-08T12:01:00.000Z",
                "routing_rule_name": "best",
                "provider": "gemini",
                "model": "gemini-2.0-flash-exp",
                "status": "error",
                "latency": 800,
                "error_details": {
                    "status_code": 429,
                    "error": {"message": "rate limited", "type": "rate_limit"},
                },
            },
        ]
    }


@pytest.fixture
def fake_metrics_text():
    return (
        '# HELP bifrost_input_tokens_total\n'
        'bifrost_input_tokens_total{routing_rule_name="best",provider="groq"} 1000\n'
        'bifrost_output_tokens_total{routing_rule_name="best",provider="groq"} 500\n'
        'bifrost_success_requests_total{routing_rule_name="best",provider="groq"} 10\n'
        'bifrost_error_requests_total{routing_rule_name="best",provider="groq"} 1\n'
    )
