"""Smoke test so CI has at least one green test on Day 1."""
from django.test import Client, TestCase


class HealthEndpointTests(TestCase):
    def test_health_returns_ok(self):
        client = Client()
        response = client.get("/api/health/")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
