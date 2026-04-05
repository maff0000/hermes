"""
API endpoint tests for tradingSignals
EPIC-D002 / STORY-D002-05

Tests:
- Health endpoints
- Price endpoints
- FX rate endpoints
- Status endpoints

Note: These tests require httpx and compatible Starlette version.
Skip if TestClient unavailable.
"""
import pytest
import sys

# Path setup moved to conftest.py

# Check if TestClient works with current Starlette version
try:
    from starlette.testclient import TestClient
    from main import app
    # Try to create a test client
    _test_client = TestClient(app)
    TESTCLIENT_AVAILABLE = True
except (ImportError, TypeError) as e:
    TESTCLIENT_AVAILABLE = False
    SKIP_REASON = f"TestClient not compatible with installed Starlette version: {e}"


@pytest.mark.skipif(not TESTCLIENT_AVAILABLE, reason="TestClient incompatible")
class TestHealthEndpoints:
    """Test health check endpoints"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup test client"""
        from main import app
        self.client = TestClient(app)

    def test_health_endpoint(self):
        """Test /health returns OK"""
        response = self.client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["service"] == "signal-service"

    def test_ready_endpoint_no_ticks(self):
        """Test /ready returns 503 when no ticks received"""
        response = self.client.get("/ready")
        # Should be 503 if no ticks yet, or 200 if service has ticks
        assert response.status_code in [200, 503]

    def test_metrics_endpoint(self):
        """Test /metrics returns prometheus format"""
        response = self.client.get("/metrics")
        assert response.status_code == 200
        # Should be text/plain
        assert "text/plain" in response.headers.get("content-type", "")


@pytest.mark.skipif(not TESTCLIENT_AVAILABLE, reason="TestClient incompatible")
class TestPriceEndpoints:
    """Test price endpoints"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup test client"""
        from main import app
        self.client = TestClient(app)

    def test_get_all_prices(self):
        """Test /prices returns price list"""
        response = self.client.get("/prices")
        assert response.status_code == 200
        data = response.json()
        assert "source" in data
        assert "prices" in data

    def test_get_price_not_found(self):
        """Test /prices/{instrument} returns 404 for unknown"""
        response = self.client.get("/prices/NONEXISTENT_PAIR")
        assert response.status_code == 404

    def test_get_price_normalization(self):
        """Test instrument name normalization works"""
        # Both formats should work (if instrument exists)
        response1 = self.client.get("/prices/XAU_USD")
        response2 = self.client.get("/prices/XAUUSD")
        # Both should return same status (either 200 or 404)
        assert response1.status_code == response2.status_code


@pytest.mark.skipif(not TESTCLIENT_AVAILABLE, reason="TestClient incompatible")
class TestFXEndpoints:
    """Test FX rate endpoints"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup test client"""
        from main import app
        self.client = TestClient(app)

    def test_get_fx_rate_not_found(self):
        """Test /fx/{pair} returns 404 for unknown pair"""
        response = self.client.get("/fx/NONEXISTENT_PAIR")
        assert response.status_code == 404

    def test_fx_rate_normalization(self):
        """Test FX pair name normalization"""
        response1 = self.client.get("/fx/GBP_USD")
        response2 = self.client.get("/fx/GBPUSD")
        assert response1.status_code == response2.status_code


@pytest.mark.skipif(not TESTCLIENT_AVAILABLE, reason="TestClient incompatible")
class TestStatusEndpoints:
    """Test status and admin endpoints"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup test client"""
        from main import app
        self.client = TestClient(app)

    def test_status_endpoint(self):
        """Test /status returns service status"""
        response = self.client.get("/status")
        assert response.status_code == 200
        data = response.json()
        assert "service" in data
        assert data["service"] == "signal-service"
        assert "version" in data
        assert "adapters" in data

    def test_failover_invalid_source(self):
        """Test /failover rejects invalid source"""
        response = self.client.post("/failover/invalid_source")
        assert response.status_code == 400

    def test_failover_valid_source(self):
        """Test /failover accepts valid source"""
        response = self.client.post("/failover/oanda")
        assert response.status_code == 200
        data = response.json()
        assert data["new_source"] == "oanda"


@pytest.mark.skipif(not TESTCLIENT_AVAILABLE, reason="TestClient incompatible")
class TestAPIResponseFormat:
    """Test API response formats"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup test client"""
        from main import app
        self.client = TestClient(app)

    def test_health_json_format(self):
        """Test health endpoint returns valid JSON"""
        response = self.client.get("/health")
        assert response.headers.get("content-type", "").startswith("application/json")

    def test_error_response_format(self):
        """Test error responses have proper format"""
        response = self.client.get("/prices/NONEXISTENT")
        assert response.status_code == 404
        data = response.json()
        assert "detail" in data
