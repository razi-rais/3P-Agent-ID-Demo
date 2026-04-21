"""
LLM Agent with Weather Tool - Uses LangChain + AWS Bedrock + Agent Identity for secure API calls
This agent demonstrates how an AI agent uses tools with Agent Identity tokens.
"""

import os
import json
import base64
import requests
import time
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS

# ⚠️  DEMO ONLY — global rate limiter. Not per-session; suitable for single-user demos only.
last_bedrock_call_time = 0
BEDROCK_RATE_LIMIT_SECONDS = 20

# LangChain imports - using try/except for graceful fallback
LANGCHAIN_AVAILABLE = False
ChatBedrock = None
tool = None

try:
    from langchain_aws import ChatBedrock
    from langchain_core.tools import tool
    from langchain_core.prompts import ChatPromptTemplate
    from langchain.agents import create_agent
    LANGCHAIN_AVAILABLE = True
    print("LangChain with AWS Bedrock loaded successfully")
except ImportError as e:
    print(f"LangChain not fully available: {e}")
    print("Running in direct mode only")

app = Flask(__name__)
CORS(app)  # ⚠️  DEMO ONLY — allows all origins. Restrict in production.

# Configuration
SIDECAR_URL = os.environ.get('SIDECAR_URL', 'http://sidecar:5000')
WEATHER_API_URL = os.environ.get('WEATHER_API_URL', 'http://weather-api:8080')
AGENT_APP_ID = os.environ.get('AGENT_APP_ID', '')
BLUEPRINT_APP_ID = os.environ.get('BLUEPRINT_APP_ID', '')
TENANT_ID = os.environ.get('TENANT_ID', '')
CLIENT_SPA_APP_ID = os.environ.get('CLIENT_SPA_APP_ID', '')
AWS_REGION = os.environ.get('AWS_REGION', 'us-east-1')
BEDROCK_MODEL_ID = os.environ.get('BEDROCK_MODEL_ID', 'anthropic.claude-3-sonnet-20240229-v1:0')

# ⚠️  DEMO ONLY — global state. Not thread-safe; suitable for single-user demos only.
debug_logs = []


def log_debug(step, message, data=None):
    """Log debug information for UI display"""
    entry = {
        "step": step,
        "message": message,
        "data": data
    }
    debug_logs.append(entry)
    print(f"[{step}] {message}")
    if data:
        print(f"    Data: {json.dumps(data, indent=2)[:500]}")


def clear_debug():
    """Clear debug logs for new request"""
    global debug_logs
    debug_logs = []


def decode_jwt_payload(token):
    """Decode JWT payload (without verification) to display claims"""
    try:
        if token.startswith('Bearer '):
            token = token[7:]
        parts = token.split('.')
        if len(parts) != 3:
            return None
        payload = parts[1]
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += '=' * padding
        decoded = base64.urlsafe_b64decode(payload)
        return json.loads(decoded)
    except Exception:
        return None


def get_agent_token():
    """Get Agent Identity token from sidecar"""
    log_debug("2.A TOKEN REQUEST", f"Requesting token for Agent: {AGENT_APP_ID}")
    
    try:
        url = f"{SIDECAR_URL}/AuthorizationHeaderUnauthenticated/graph-app?AgentIdentity={AGENT_APP_ID}"
        log_debug("2.B SIDECAR CALL", f"Sidecar URL: {url}")
        
        response = requests.get(url, timeout=30, headers={"Host": "localhost"})
        response.raise_for_status()
        
        result = response.json()
        auth_header = result.get('authorizationHeader', '')
        
        if auth_header:
            claims = decode_jwt_payload(auth_header)
            if claims:
                global _last_tr_claims
                # Pass through ALL claims from the JWT (same as OBO flow)
                _last_tr_claims = claims
                log_debug("2.C TOKEN RECEIVED", "Got Agent Identity token (TR) from sidecar", {
                    "_jwt_token": {
                        "type": "tr",
                        "title": "\U0001f512 TR \u2014 Autonomous Agent Token (App-Only / Client Credentials)",
                        "css": "tr",
                        "hl": "highlight-purple",
                        "claims": claims
                    }
                })
        
        return auth_header
    except Exception as e:
        log_debug("2. TOKEN ERROR", f"Failed to get token: {str(e)}")
        return None


def get_agent_token_obo(user_token=None):
    """Get Agent Identity token via OBO (On-Behalf-Of) flow using authenticated sidecar endpoint.
    
    OBO flow:
      1. User authenticates → obtains Tc (user access token)
      2. Agent presents Tc to sidecar via /AuthorizationHeader (authenticated endpoint)
      3. Sidecar validates Tc, acquires T1 (blueprint token), performs OBO exchange
      4. Returns interactive agent token (TR) that acts on behalf of the user
    
    Docs: https://learn.microsoft.com/en-us/entra/agent-id/identity-platform/agent-on-behalf-of-oauth-flow
    SDK:  https://learn.microsoft.com/en-us/entra/msidweb/agent-id-sdk/endpoints
    """
    log_debug("OBO 2.A TOKEN REQUEST", f"Requesting OBO token for Agent: {AGENT_APP_ID}", {
        "endpoint": "/AuthorizationHeader/graph (authenticated)",
        "flow": "User Token (Tc) → Sidecar → T1 (Blueprint) → OBO Exchange → TR (Interactive Agent)",
        "vs_autonomous": "/AuthorizationHeaderUnauthenticated/graph-app (no user token needed)",
        "bearer_token_sent": "Tc (user's access token) — sent in Authorization header",
        "why_bearer": "Sidecar needs user's Tc to perform OBO exchange on behalf of that user"
    })
    
    try:
        # OBO uses the AUTHENTICATED endpoint (vs Unauthenticated for autonomous)
        url = f"{SIDECAR_URL}/AuthorizationHeader/graph?AgentIdentity={AGENT_APP_ID}"
        tc_snippet = ""
        if user_token:
            raw = user_token.replace("Bearer ", "") if user_token.startswith("Bearer ") else user_token
            tc_snippet = raw[:32] + "..." + raw[-16:] if len(raw) > 52 else raw
        log_debug("OBO 2.B ENDPOINT", f"Authenticated sidecar URL: {url}", {
            "http_method": "GET",
            "authorization_header": f"Bearer {tc_snippet}" if tc_snippet else "(none — will fail)",
            "header_contains": "Tc — the user's access token obtained via MSAL.js sign-in",
            "note": "Unlike /AuthorizationHeaderUnauthenticated, this endpoint REQUIRES a Bearer token (Tc)",
            "docs": "https://learn.microsoft.com/en-us/entra/msidweb/agent-id-sdk/endpoints"
        })
        
        headers = {"Host": "localhost"}
        if user_token:
            if not user_token.startswith("Bearer "):
                headers["Authorization"] = f"Bearer {user_token}"
            else:
                headers["Authorization"] = user_token
            tc_raw = user_token.replace("Bearer ", "") if user_token.startswith("Bearer ") else user_token
            tc_preview = tc_raw[:32] + "..." + tc_raw[-16:] if len(tc_raw) > 52 else tc_raw
            tc_claims_preview = decode_jwt_payload(tc_raw) or {}
            tc_sub = tc_claims_preview.get('name') or tc_claims_preview.get('sub') or tc_claims_preview.get('oid') or '?'
            tc_aud = tc_claims_preview.get('aud', '?')
            log_debug("OBO 2.C USER TOKEN", f"Sending Tc as Bearer → sidecar will use it for OBO exchange", {
                "bearer_header": f"Authorization: Bearer {tc_preview}",
                "token_owner": f"{tc_sub} (from Tc claims)",
                "token_audience": f"{tc_aud}",
                "sidecar_will_do": "1) Validate Tc  2) Acquire T1 (client_credentials)  3) OBO exchange: Tc + T1 → TR",
                "obo_grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "obo_assertion": "Tc (this Bearer token)",
                "obo_client_assertion": "T1 (Blueprint's client-credentials token)",
                "obo_result": "TR — interactive agent token that acts on behalf of the user"
            })
        else:
            log_debug("OBO 2.C NO USER TOKEN", "No user token provided - sidecar may reject or fall back to app-only", {
                "explanation": "In production, user signs in via MSAL → obtains Tc → passes to agent",
                "required_audience": f"api://{BLUEPRINT_APP_ID}" if BLUEPRINT_APP_ID else "api://<blueprint-client-id>"
            })
        
        response = requests.get(url, timeout=30, headers=headers)
        
        if response.status_code == 200:
            result = response.json()
            auth_header = result.get('authorizationHeader', '')
            
            if auth_header:
                claims = decode_jwt_payload(auth_header)
                if claims:
                    global _last_tr_claims
                    # Pass through ALL claims from the JWT
                    _last_tr_claims = claims
                    log_debug("OBO 2.D TOKEN RECEIVED", "Got interactive agent token (TR) via OBO exchange", {
                        "_jwt_token": {
                            "type": "tr",
                            "title": "\U0001f4aa TR \u2014 Agent OBO Token (Interactive)",
                            "css": "tr",
                            "hl": "highlight-green",
                            "claims": claims
                        }
                    })
            
            return auth_header
        else:
            error_text = response.text[:500]
            log_debug("OBO 2.D ENDPOINT RESPONSE", f"Sidecar returned HTTP {response.status_code}", {
                "status_code": response.status_code,
                "response": error_text,
                "explanation": "Expected: OBO requires a valid user token (Tc) in Authorization header. "
                               "Without it, the authenticated endpoint may return 400/401.",
                "production_fix": "User signs in via MSAL → app receives Tc → passes Bearer Tc to this endpoint"
            })
            return None
    except Exception as e:
        log_debug("OBO 2. ERROR", f"Failed to get OBO token: {str(e)}")
        return None


def get_t1_token_claims():
    """Get the app-only (T1) token claims for display purposes.
    
    In the OBO flow, the sidecar uses T1 (blueprint's client-credentials token)
    as the client_assertion when performing the OBO exchange. We fetch it separately
    here so we can display it in the token viewer alongside Tc and TR.
    
    In the Autonomous flow, T1 IS the final token (returned by get_agent_token).
    """
    try:
        url = f"{SIDECAR_URL}/AuthorizationHeaderUnauthenticated/graph-app?AgentIdentity={AGENT_APP_ID}"
        response = requests.get(url, timeout=30, headers={"Host": "localhost"})
        response.raise_for_status()
        result = response.json()
        auth_header = result.get('authorizationHeader', '')
        if auth_header:
            return decode_jwt_payload(auth_header)
    except Exception:
        pass
    return None


def call_weather_api(city: str, token: str, token_label: str = "TR", is_obo: bool = False):
    """Call Weather API with Agent Identity token"""
    log_debug("3.A API CALL", f"Calling Weather API for: {city}")
    
    try:
        url = f"{WEATHER_API_URL}/weather?city={city}"
        headers = {"Authorization": token}
        
        # Build trimmed Bearer snippet for display
        raw = token.replace("Bearer ", "") if token.startswith("Bearer ") else token
        snippet = raw[:32] + "..." + raw[-16:] if len(raw) > 52 else raw
        if is_obo:
            token_desc = "TR — Interactive Agent Token (acts on behalf of user via OBO)"
        else:
            token_desc = "TR — Autonomous Agent Token (app-only, no user context) per MS docs"
        
        log_debug("3.B API URL", f"URL: {url}", {
            "token_sent": token_label,
            "token_description": token_desc,
            "authorization_header": f"Authorization: Bearer {snippet}",
            "why": f"Weather API validates {token_label} to authorize the agent's request"
        })
        
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        weather_data = response.json()
        log_debug("3.C API RESPONSE", "Got weather data from API", weather_data)
        
        return weather_data
    except Exception as e:
        log_debug("3. WEATHER ERROR", f"API call failed: {str(e)}")
        return None


# ============================================
# Weather Function (works with or without LangChain)
# ============================================
# Global holder for user_token when OBO mode is active (set per-request)
_current_user_token = None
# Store last TR (result token) claims for display
_last_tr_claims = None


def get_weather_data(city: str, user_token=None) -> str:
    """
    Get the current weather for a city.
    Uses Agent Identity to securely authenticate with the Weather API.
    If user_token is provided, uses OBO flow via authenticated sidecar endpoint.
    """
    is_obo = user_token is not None
    flow_label = "OBO" if is_obo else "Autonomous"
    log_debug("1.B TOOL CALL", f"Weather function called for city: {city} (flow: {flow_label})")
    
    # Step 1: Get Agent Identity token from sidecar
    if is_obo:
        token = get_agent_token_obo(user_token=user_token)
    else:
        token = get_agent_token()
    
    if not token:
        return f"Error: Could not authenticate with Agent Identity ({flow_label}). The sidecar may not be running."
    
    # Step 2: Call Weather API with the token
    token_label = "TR"
    weather = call_weather_api(city, token, token_label=token_label, is_obo=is_obo)
    if not weather:
        return f"Error: Could not get weather data for {city}. The API may have rejected the token."
    
    # Step 3: Format response
    result = f"""Weather for {weather.get('city', city)}:
- Temperature: {weather.get('temperature', 'N/A')}°{weather.get('temperature_unit', 'F')}
- Condition: {weather.get('condition', 'N/A')}
- Humidity: {weather.get('humidity', 'N/A')}%
- Wind Speed: {weather.get('wind_speed', 'N/A')} {weather.get('wind_unit', 'mph')}
- Timestamp: {weather.get('timestamp', 'N/A')} ({weather.get('timezone', 'UTC')})
- Data Source: {weather.get('data_source', 'Weather API')}
- Authentication: Validated by {weather.get('validated_by', 'Agent Identity Token')}
- Agent App ID: {weather.get('agent_app_id', 'N/A')}
- Token Flow: {flow_label}"""
    
    log_debug("4. TOOL RESULT", f"Weather data retrieved ({flow_label})", {"result": result})
    return result


# Create LangChain tool wrappers if available
if LANGCHAIN_AVAILABLE and tool is not None:
    @tool
    def get_weather(city: str) -> str:
        """
        Get real-time current weather data for any city using a live Weather API.
        
        IMPORTANT: You MUST use this tool to get weather information. Do NOT provide
        weather data from your training data or general knowledge. ALWAYS call this
        tool when the user asks about weather, temperature, conditions, or forecast.
        
        Args:
            city: The name of the city (e.g., "Seattle", "New York", "Tokyo")
        
        Returns:
            Current weather including temperature, condition, humidity, wind speed.
        """
        # Check if OBO mode is active via global token holder
        result = get_weather_data(city, user_token=_current_user_token)
        return result


# ============================================
# LangChain Agent Setup with AWS Bedrock
# ============================================
def create_weather_agent():
    """Create LangChain agent with weather tool using AWS Bedrock"""
    
    print(f"[AWS] Creating ChatBedrock instance")
    print(f"[AWS] Model: {BEDROCK_MODEL_ID}")
    print(f"[AWS] Region: {AWS_REGION}")
    
    # Initialize AWS Bedrock LLM
    llm = ChatBedrock(
        model_id=BEDROCK_MODEL_ID,
        region_name=AWS_REGION,
        model_kwargs={
            "temperature": 0.7,
            "max_tokens": 2048
        }
    )
    
    print(f"[AWS] ✓ ChatBedrock instance created successfully")
    
    # Define tools
    tools = [get_weather]

    # Use LangChain 1.0+ agent API
    agent = create_agent(llm, tools)

    return agent


def process_with_langchain(user_query: str):
    """Process query using LangChain agent with AWS Bedrock"""
    global last_bedrock_call_time
    
    # Rate limiting - wait if needed
    time_since_last_call = time.time() - last_bedrock_call_time
    if time_since_last_call < BEDROCK_RATE_LIMIT_SECONDS:
        wait_time = BEDROCK_RATE_LIMIT_SECONDS - time_since_last_call
        log_debug("0. RATE LIMIT", f"Waiting {wait_time:.1f} seconds to avoid AWS throttling...")
        print(f"[AWS] Rate limit: waiting {wait_time:.1f} seconds...")
        time.sleep(wait_time)
    
    log_debug("0.A START", f"User query: {user_query}")
    log_debug("0.B BEDROCK", f"Sending query to AWS Bedrock (model: {BEDROCK_MODEL_ID})")
    
    print(f"\n{'='*60}")
    print(f"[AWS] Processing query with Bedrock LLM")
    print(f"[AWS] Query: {user_query}")
    print(f"[AWS] Model: {BEDROCK_MODEL_ID}")
    print(f"[AWS] Region: {AWS_REGION}")
    print(f"{'='*60}\n")
    
    try:
        agent = create_weather_agent()
        log_debug("0.C AGENT READY", f"LangChain agent created with AWS Bedrock ({BEDROCK_MODEL_ID})")
        
        print(f"[AWS] Invoking Bedrock API...")
        
        # Add system message to encourage ONE tool call
        from langchain_core.messages import SystemMessage
        system_msg = SystemMessage(content="You have access to a get_weather tool. When users ask about weather, call the get_weather tool ONCE with the city name, then provide a natural response using the data returned. Do NOT call the tool multiple times.")
        
        # Limit recursion to prevent infinite loops
        result = agent.invoke(
            {"messages": [system_msg, ("human", user_query)]},
            {"recursion_limit": 10}  # Max 10 steps to prevent loops
        )
        
        # Check messages for tool calls (just detection, no duplicate logging)
        messages = result.get("messages", [])
        tool_called = False
        city = None
        output = None
        
        for msg in messages:
            if hasattr(msg, 'tool_calls') and msg.tool_calls:
                for tool_call in msg.tool_calls:
                    tool_called = True
                    city = tool_call.get('args', {}).get('city', 'unknown')
                    # Tool execution already logged everything, don't duplicate
        
        # Fallback: If LLM didn't call tool but query is about weather, manually call it
        if not tool_called and any(keyword in user_query.lower() for keyword in ['weather', 'temperature', 'forecast', 'condition']):
            log_debug("1. FALLBACK", "LLM didn't call tool - manually extracting city and calling weather tool")
            
            # Extract city from query
            import re
            clean_query = user_query.strip().rstrip('?').rstrip('.')
            words = clean_query.split()
            
            # Try common patterns
            city = None
            if ' in ' in clean_query.lower():
                city = clean_query.lower().split(' in ')[-1].strip()
            elif ' for ' in clean_query.lower():
                city = clean_query.lower().split(' for ')[-1].strip()
            elif len(words) >= 2:
                city = words[-1]  # Last word
            
            if city:
                city = city.capitalize()
                log_debug("2. CITY DETECTED", f"Extracted city: {city}")
                
                # Manually call the weather tool
                weather_result = get_weather_data(city)
                output = weather_result
                tool_called = True
            else:
                log_debug("2. NO CITY", "Could not extract city from query")
        
        if not tool_called:
            log_debug("1. NO TOOL", "LLM responded without calling weather tool (may have used general knowledge)")
        
        # Extract final message from LangGraph response
        if output is None:
            output = result.get("messages", [])[-1].content if result.get("messages") else "No response"
        
        print(f"[AWS] ✓ Received response from Bedrock")
        print(f"[AWS] Response length: {len(output)} characters")
        
        log_debug("5. COMPLETE", "AWS Bedrock agent finished processing")
        
        # Update rate limit timestamp
        last_bedrock_call_time = time.time()
        
        return {
            "response": output,
            "debug": debug_logs,
            "success": True,
            "agent_type": "bedrock"
        }
    except Exception as e:
        print(f"[AWS] ✗ Bedrock API call failed: {str(e)}")
        import traceback
        traceback.print_exc()
        log_debug("ERROR", f"AWS Bedrock agent failed: {str(e)}")
        return {
            "response": f"Agent error: {str(e)}",
            "debug": debug_logs,
            "success": False,
            "agent_type": "bedrock"
        }


def _extract_city(user_query: str) -> str:
    """Extract city name from a user query about weather."""
    import re
    clean_query = user_query.strip().rstrip('?').rstrip('.')
    
    match = re.search(r'\bin\s+([A-Za-z][A-Za-z\s]*?)$', clean_query, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    
    match = re.search(r'\bfor\s+([A-Za-z][A-Za-z\s]*?)$', clean_query, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    
    words = clean_query.split()
    if words:
        last_word = words[-1]
        common_words = {'weather', 'what', 'is', 'the', 'how', 'today', 'now', 'like'}
        if last_word.lower() not in common_words:
            return last_word
    
    return "Seattle"


def process_without_llm(user_query: str, user_token=None):
    """Process query without LLM (direct tool call).
    If user_token is provided, uses OBO flow."""
    is_obo = user_token is not None
    flow_label = "OBO" if is_obo else "Autonomous"
    log_debug("0.A START", f"Processing query (Direct + {flow_label}): {user_query}")
    
    if is_obo:
        log_debug("0.B OBO MODE", "User token provided — will use authenticated sidecar endpoint", {
            "endpoint": "/AuthorizationHeader/graph (requires Bearer token)",
            "docs": "https://learn.microsoft.com/en-us/entra/msidweb/agent-id-sdk/endpoints"
        })
    
    city = _extract_city(user_query)
    
    log_debug("1.A DIRECT CALL", f"Calling weather function directly for: {city} (flow: {flow_label})")
    weather_result = get_weather_data(city, user_token=user_token)
    
    flow_badge = "🔄 OBO" if is_obo else "⚡ Autonomous"
    response = f"""Here's what I found:

{weather_result}

✅ *Securely retrieved using Agent Identity ({flow_badge})*"""
    
    log_debug("5. COMPLETE", f"Query processed (Direct + {flow_label})")
    
    return {
        "response": response,
        "debug": debug_logs,
        "success": True,
        "agent_type": "direct",
        "token_flow": "obo" if is_obo else "autonomous"
    }


def check_bedrock_available():
    """Check if AWS credentials are configured"""
    try:
        import boto3
        # Try to create a bedrock-runtime client
        print(f"[AWS] Checking Bedrock availability in region: {AWS_REGION}")
        bedrock = boto3.client('bedrock-runtime', region_name=AWS_REGION)
        print(f"[AWS] ✓ Bedrock client created successfully")
        print(f"[AWS] Using credentials: {os.environ.get('AWS_ACCESS_KEY_ID', 'NOT SET')[:8]}...")
        return True
    except Exception as e:
        print(f"[AWS] ✗ Bedrock not available: {e}")
        return False


# ============================================
# Flask Routes
# ============================================
@app.route('/')
def index():
    """Serve the chat UI"""
    return render_template('index.html')


@app.route('/api/chat', methods=['POST'])
def chat():
    """Handle chat messages.
    
    Accepts:
        message: str - user query
        llm_mode: 'direct' | 'bedrock' - LLM processing mode
        token_flow: 'autonomous' | 'obo' - token acquisition flow
        user_token: str | null - MSAL user access token (required for OBO)
    """
    global _current_user_token
    data = request.json
    user_message = data.get('message', '')
    llm_mode = data.get('llm_mode', data.get('mode', 'direct'))  # backward compat
    token_flow = data.get('token_flow', 'autonomous')
    user_token = data.get('user_token', None)
    
    if not user_message:
        return jsonify({"error": "No message provided"}), 400
    
    # For OBO flow, user_token is required
    if token_flow == 'obo' and not user_token:
        return jsonify({"error": "OBO flow requires a user token. Please sign in first."}), 400
    
    # Set global token for LangChain tool access (tools can't receive params directly)
    global _last_tr_claims
    _current_user_token = user_token if token_flow == 'obo' else None
    _last_tr_claims = None  # Reset for each request
    clear_debug()  # Clear debug logs at start of each request
    
    # Decode Tc (user token) claims for display — pass ALL claims
    tc_claims = None
    if user_token and token_flow == 'obo':
        tc_claims = decode_jwt_payload(user_token)
        if tc_claims:
            log_debug("OBO 0.A USER TOKEN (Tc)", "Decoded user access token from MSAL sign-in", {
                "_jwt_token": {
                    "type": "tc",
                    "title": "\U0001f511 Tc \u2014 User Token (from MSAL sign-in)",
                    "css": "tc",
                    "hl": "highlight",
                    "claims": tc_claims
                }
            })
    
    try:
        if llm_mode == 'bedrock' and LANGCHAIN_AVAILABLE and check_bedrock_available():
            result = process_with_langchain(user_message)
            # Override agent_type to reflect both dimensions
            result['token_flow'] = token_flow
        else:
            result = process_without_llm(user_message, user_token=_current_user_token)
    finally:
        _current_user_token = None  # Always clear after request
    
    # Attach token claims for display (both OBO and Autonomous)
    # Note: full JWT claims are now embedded directly in debug log entries
    # (Tc at step OBO 0.A, T1 inserted before TR, TR at step OBO 2.D)
    if token_flow == 'obo':
        # Fetch T1 claims and insert into debug log BEFORE the TR entry
        t1_claims = get_t1_token_claims()
        if t1_claims:
            t1_entry = {
                "step": "OBO 2.C T1 (Blueprint)",
                "message": "Blueprint app-only token used as client_assertion in OBO exchange",
                "data": {
                    "_jwt_token": {
                        "type": "t1",
                        "title": "\U0001f4dc T1 \u2014 Blueprint Token (App-Only / Client Credentials)",
                        "css": "t1",
                        "hl": "highlight-purple",
                        "claims": t1_claims
                    }
                }
            }
            # Find OBO 2.D (TR) entry and insert T1 right before it
            tr_idx = next((i for i, e in enumerate(result['debug']) if 'OBO 2.D' in e.get('step', '')), None)
            if tr_idx is not None:
                result['debug'].insert(tr_idx, t1_entry)
            else:
                result['debug'].append(t1_entry)
    _last_tr_claims = None
    
    # Add doc links entry at end of debug flow
    result['debug'].append({
        "step": "DOCS",
        "message": "_doc_links",
        "data": None
    })
    
    return jsonify(result)


@app.route('/api/status', methods=['GET'])
def status():
    """Check service status"""
    bedrock_ready = check_bedrock_available()
    return jsonify({
        "bedrock_available": bedrock_ready,
        "aws_region": AWS_REGION,
        "bedrock_model": BEDROCK_MODEL_ID,
        "sidecar_url": SIDECAR_URL,
        "agent_app_id": AGENT_APP_ID[:8] + "..." if AGENT_APP_ID else "not set"
    })


@app.route('/api/config', methods=['GET'])
def config():
    """Return MSAL configuration for browser-side OBO sign-in.
    Only exposes non-secret values needed by MSAL.js."""
    # Dynamically determine redirect URI — respect proxy headers from Container Apps
    scheme = request.headers.get('X-Forwarded-Proto', request.scheme)
    host = request.headers.get('X-Forwarded-Host', request.host)
    redirect_uri = f"{scheme}://{host}"
    return jsonify({
        "tenant_id": TENANT_ID,
        "blueprint_app_id": BLUEPRINT_APP_ID,
        "client_spa_app_id": CLIENT_SPA_APP_ID,
        "agent_app_id": AGENT_APP_ID,
        # Scope targets Blueprint app — sidecar validates aud matches its ClientId (Blueprint)
        # Ref: https://blog.christianposta.com/entra-agent-id-agw/PART-2.html
        "obo_scopes": [f"api://{BLUEPRINT_APP_ID}/access_as_user"] if BLUEPRINT_APP_ID else [],
        "authority": f"https://login.microsoftonline.com/{TENANT_ID}" if TENANT_ID else "",
        "redirect_uri": redirect_uri,
    })


@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "service": "LLM Weather Agent (AWS Bedrock)",
        "agent_app_id": AGENT_APP_ID[:8] + "..." if AGENT_APP_ID else "not set"
    })


# ============================================


if __name__ == '__main__':
    print("=" * 60)
    print("  3P Agent Identity Demo - AWS Bedrock Edition")
    print("=" * 60)
    print(f"  Sidecar URL: {SIDECAR_URL}")
    print(f"  Weather API: {WEATHER_API_URL}")
    print(f"  Agent App ID: {AGENT_APP_ID[:8]}..." if AGENT_APP_ID else "  Agent App ID: NOT SET")
    print(f"  AWS Region: {AWS_REGION}")
    print(f"  Bedrock Model: {BEDROCK_MODEL_ID}")
    print("=" * 60)
    print("  Open http://localhost:3001 in your browser")
    print("=" * 60)
    
    app.run(host='0.0.0.0', port=3000, debug=True)
