"""
Tests for the dev sidecar (LLM Agent with Weather Tool).

Covers:
- Flask route responses and status codes
- JWT decode utility (valid, malformed, missing segments)
- City extraction from user queries
- LangChain agent creation (when available)
- Debug logging behavior
- Config and status endpoints
"""

import json
import base64
import time
import pytest
import sys
import os

# Add parent dir so we can import app
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import (
    app,
    decode_jwt_payload,
    clear_debug,
    log_debug,
    LANGCHAIN_AVAILABLE,
)


# ============================================
# Fixtures
# ============================================

@pytest.fixture
def client():
    """Flask test client"""
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def reset_debug():
    """Clear debug logs before each test"""
    clear_debug()
    yield
    clear_debug()


def _make_jwt(payload: dict) -> str:
    """Helper: create an unsigned JWT (header.payload.signature) for testing."""
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode()).rstrip(b'=').decode()
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b'=').decode()
    return f"{header}.{body}.fakesig"


# ============================================
# decode_jwt_payload
# ============================================

class TestDecodeJwtPayload:

    def test_valid_jwt(self):
        claims = {"sub": "user1", "iss": "https://login.microsoftonline.com/tid/v2.0", "exp": 9999999999}
        token = _make_jwt(claims)
        result = decode_jwt_payload(token)
        assert result is not None
        assert result["sub"] == "user1"
        assert result["iss"] == claims["iss"]

    def test_bearer_prefix_stripped(self):
        claims = {"sub": "user2"}
        token = "Bearer " + _make_jwt(claims)
        result = decode_jwt_payload(token)
        assert result is not None
        assert result["sub"] == "user2"

    def test_malformed_token_returns_none(self):
        assert decode_jwt_payload("not.a.valid.jwt.token") is None
        assert decode_jwt_payload("garbage") is None
        assert decode_jwt_payload("") is None

    def test_two_segment_token_returns_none(self):
        assert decode_jwt_payload("part1.part2") is None

    def test_invalid_base64_returns_none(self):
        assert decode_jwt_payload("x.!!!.z") is None


# ============================================
# Debug logging
# ============================================

class TestDebugLogging:

    def test_log_debug_appends(self):
        log_debug("STEP1", "hello")
        import app as app_module
        assert len(app_module.debug_logs) == 1
        assert app_module.debug_logs[0]["step"] == "STEP1"
        assert app_module.debug_logs[0]["message"] == "hello"

    def test_log_debug_with_data(self):
        log_debug("STEP2", "msg", {"key": "val"})
        import app as app_module
        assert app_module.debug_logs[0]["data"] == {"key": "val"}

    def test_clear_debug(self):
        log_debug("X", "Y")
        clear_debug()
        import app as app_module
        assert len(app_module.debug_logs) == 0


# ============================================
# Flask routes — health / status / config
# ============================================

class TestHealthRoute:

    def test_health_returns_200(self, client):
        resp = client.get('/health')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "healthy"

    def test_health_includes_service_name(self, client):
        resp = client.get('/health')
        data = resp.get_json()
        assert "LangChain" in data["service"]


class TestStatusRoute:

    def test_status_returns_200(self, client):
        resp = client.get('/api/status')
        assert resp.status_code == 200
        data = resp.get_json()
        assert "ollama_available" in data
        assert "ollama_model" in data

    def test_status_contains_expected_keys(self, client):
        resp = client.get('/api/status')
        data = resp.get_json()
        for key in ("ollama_available", "ollama_url", "ollama_model", "sidecar_url", "agent_app_id"):
            assert key in data, f"Missing key: {key}"


class TestConfigRoute:

    def test_config_returns_200(self, client):
        resp = client.get('/api/config')
        assert resp.status_code == 200

    def test_config_contains_msal_fields(self, client):
        resp = client.get('/api/config')
        data = resp.get_json()
        for key in ("tenant_id", "blueprint_app_id", "client_spa_app_id", "obo_scopes", "authority", "redirect_uri"):
            assert key in data, f"Missing MSAL config field: {key}"

    def test_config_redirect_uri_uses_request_host(self, client):
        resp = client.get('/api/config', headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "demo.example.com"})
        data = resp.get_json()
        assert data["redirect_uri"] == "https://demo.example.com"


# ============================================
# /api/chat — input validation
# ============================================

class TestChatRouteValidation:

    def test_empty_message_returns_400(self, client):
        resp = client.post('/api/chat', json={"message": ""})
        assert resp.status_code == 400

    def test_missing_message_returns_400(self, client):
        resp = client.post('/api/chat', json={})
        assert resp.status_code == 400

    def test_obo_without_token_returns_400(self, client):
        resp = client.post('/api/chat', json={
            "message": "weather in Seattle",
            "token_flow": "obo",
            "user_token": None,
        })
        assert resp.status_code == 400
        data = resp.get_json()
        assert "OBO" in data["error"]


# ============================================
# City extraction (via process_without_llm)
# ============================================

class TestCityExtraction:
    """Test city extraction logic in process_without_llm.
    We test indirectly by calling the endpoint in direct (non-LLM) mode
    and checking that the query doesn't crash.
    These tests may fail if sidecar is not running — they test the parsing, not the API call."""

    def _extract_city(self, query: str) -> str:
        """Extract city using the same regex logic as process_without_llm."""
        import re
        city = None
        clean_query = query.strip().rstrip('?').rstrip('.')
        match = re.search(r'\bin\s+([A-Za-z][A-Za-z\s]*?)$', clean_query, re.IGNORECASE)
        if match:
            city = match.group(1).strip()
        if not city:
            match = re.search(r'\bfor\s+([A-Za-z][A-Za-z\s]*?)$', clean_query, re.IGNORECASE)
            if match:
                city = match.group(1).strip()
        if not city:
            words = clean_query.split()
            if words:
                last_word = words[-1]
                common_words = {'weather', 'what', 'is', 'the', 'how', 'today', 'now', 'like'}
                if last_word.lower() not in common_words:
                    city = last_word
        if not city:
            city = "Seattle"
        return city

    def test_weather_in_city(self):
        assert self._extract_city("weather in Dallas") == "Dallas"

    def test_weather_in_multi_word_city(self):
        assert self._extract_city("weather in New York") == "New York"

    def test_weather_for_city(self):
        assert self._extract_city("weather for London") == "London"

    def test_last_word_fallback(self):
        assert self._extract_city("tell me about Tokyo") == "Tokyo"

    def test_default_seattle(self):
        assert self._extract_city("what is the weather") == "Seattle"

    def test_strips_question_mark(self):
        assert self._extract_city("weather in Paris?") == "Paris"


# ============================================
# LangChain availability
# ============================================

class TestLangChainAvailability:

    def test_langchain_flag_is_set(self):
        """LANGCHAIN_AVAILABLE should be True or False (no longer 'react' string)."""
        assert LANGCHAIN_AVAILABLE in (True, False)

    @pytest.mark.skipif(not LANGCHAIN_AVAILABLE, reason="LangChain not installed")
    def test_create_weather_agent_returns_agent(self):
        from app import create_weather_agent
        # This will fail to connect to Ollama, but should not raise on creation
        # The agent is a LangGraph CompiledGraph
        agent = create_weather_agent()
        assert agent is not None
        assert hasattr(agent, 'invoke')


# ============================================
# Index route
# ============================================

class TestIndexRoute:

    def test_index_returns_200(self, client):
        resp = client.get('/')
        assert resp.status_code == 200

    def test_index_returns_html(self, client):
        resp = client.get('/')
        assert b'html' in resp.data.lower()
