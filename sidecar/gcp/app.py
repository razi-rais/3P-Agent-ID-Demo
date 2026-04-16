"""
LLM Agent with Weather Tool - Uses LangChain + Google Vertex AI + Agent Identity for secure API calls
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
last_vertexai_call_time = 0
VERTEXAI_RATE_LIMIT_SECONDS = 20

# LangChain imports - using try/except for graceful fallback
LANGCHAIN_AVAILABLE = False
ChatVertexAI = None
tool = None

try:
    from langchain_google_vertexai import ChatVertexAI
    from langchain_core.tools import tool
    from langchain_core.prompts import ChatPromptTemplate
    from langgraph.prebuilt import create_react_agent
    LANGCHAIN_AVAILABLE = True
    print("LangChain with Google Vertex AI loaded successfully")
except ImportError as e:
    print(f"LangChain not fully available: {e}")
    print("Running in direct mode only")

app = Flask(__name__)
CORS(app)

# Configuration
SIDECAR_URL = os.environ.get('SIDECAR_URL', 'http://sidecar:5000')
WEATHER_API_URL = os.environ.get('WEATHER_API_URL', 'http://weather-api:8080')
AGENT_APP_ID = os.environ.get('AGENT_APP_ID', '')
GCP_PROJECT_ID = os.environ.get('GCP_PROJECT_ID', '')
GCP_LOCATION = os.environ.get('GCP_LOCATION', 'us-central1')
VERTEXAI_MODEL_ID = os.environ.get('VERTEXAI_MODEL_ID', 'gemini-1.0-pro-002')

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
        url = f"{SIDECAR_URL}/AuthorizationHeaderUnauthenticated/graph?AgentIdentity={AGENT_APP_ID}"
        log_debug("2.B REQUEST URL", f"Sidecar URL: {url}")
        
        response = requests.get(url, timeout=30, headers={"Host": "localhost"})
        response.raise_for_status()
        
        result = response.json()
        auth_header = result.get('authorizationHeader', '')
        
        if auth_header:
            claims = decode_jwt_payload(auth_header)
            if claims:
                display_claims = {
                    "aud": claims.get("aud", "N/A"),
                    "iss": claims.get("iss", "N/A"),
                    "app_displayname": claims.get("app_displayname", "N/A"),
                    "appid": claims.get("appid", "N/A"),
                    "oid": claims.get("oid", "N/A"),
                    "roles": claims.get("roles", []),
                    "tid": claims.get("tid", "N/A"),
                    "exp": claims.get("exp", "N/A"),
                    "iat": claims.get("iat", "N/A"),
                }
                log_debug("2.C TOKEN RECEIVED", "Got Agent Identity token from sidecar", {"jwt_claims": display_claims})
        
        return auth_header
    except Exception as e:
        log_debug("2. TOKEN ERROR", f"Failed to get token: {str(e)}")
        return None


def call_weather_api(city: str, token: str):
    """Call Weather API with Agent Identity token"""
    log_debug("3.A API CALL", f"Calling Weather API for: {city}")
    
    try:
        url = f"{WEATHER_API_URL}/weather?city={city}"
        headers = {"Authorization": token}
        
        log_debug("3.B API URL", f"URL: {url}", {"headers": "Authorization: Bearer <token>"})
        
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
def get_weather_data(city: str) -> str:
    """
    Get the current weather for a city.
    This function uses Agent Identity to securely authenticate with the Weather API.
    """
    log_debug("1. TOOL CALLED", f"Weather function called for city: {city}")
    
    # Step 1: Get Agent Identity token from sidecar
    token = get_agent_token()
    if not token:
        return "Error: Could not authenticate with Agent Identity. The sidecar may not be running."
    
    # Step 2: Call Weather API with the token
    weather = call_weather_api(city, token)
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
- Agent App ID: {weather.get('agent_app_id', 'N/A')}"""
    
    log_debug("4. TOOL RESULT", "Weather data retrieved", {"result": result})
    return result


# Create LangChain tool wrapper if available
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
        # Don't log here - get_weather_data already logs everything
        result = get_weather_data(city)
        return result


# ============================================
# LangChain Agent Setup with Google Vertex AI
# ============================================
def create_weather_agent():
    """Create LangChain agent with weather tool using Google Vertex AI"""
    
    print(f"[Vertex AI] Creating ChatVertexAI instance")
    print(f"[Vertex AI] Model: {VERTEXAI_MODEL_ID}")
    print(f"[Vertex AI] Project: {GCP_PROJECT_ID}")
    print(f"[Vertex AI] Location: {GCP_LOCATION}")
    
    # Initialize Google Vertex AI LLM
    llm = ChatVertexAI(
        model=VERTEXAI_MODEL_ID,
        project=GCP_PROJECT_ID,
        location=GCP_LOCATION,
        temperature=0.7,
        max_tokens=2048,
    )
    
    print(f"[Vertex AI] ✓ ChatVertexAI instance created successfully")
    
    # Define tools
    tools = [get_weather]
    
    # Bind tools to LLM
    llm_with_tools = llm.bind_tools(tools)
    
    # Use LangGraph ReAct agent
    from langgraph.prebuilt import create_react_agent
    agent = create_react_agent(llm_with_tools, tools)
    
    return agent


def process_with_langchain(user_query: str):
    """Process query using LangChain agent with Google Vertex AI"""
    global last_vertexai_call_time
    
    # Rate limiting - wait if needed
    time_since_last_call = time.time() - last_vertexai_call_time
    if time_since_last_call < VERTEXAI_RATE_LIMIT_SECONDS:
        wait_time = VERTEXAI_RATE_LIMIT_SECONDS - time_since_last_call
        log_debug("0. RATE LIMIT", f"Waiting {wait_time:.1f} seconds to avoid throttling...")
        print(f"[Gemini] Rate limit: waiting {wait_time:.1f} seconds...")
        time.sleep(wait_time)
    
    clear_debug()
    log_debug("0. START", f"User query: {user_query}")
    log_debug("0. VERTEX AI", f"Sending query to Google Vertex AI (model: {VERTEXAI_MODEL_ID})")
    
    print(f"\n{'='*60}")
    print(f"[Vertex AI] Processing query with Vertex AI LLM")
    print(f"[Vertex AI] Query: {user_query}")
    print(f"[Vertex AI] Model: {VERTEXAI_MODEL_ID}")
    print(f"[Vertex AI] Project: {GCP_PROJECT_ID}")
    print(f"[Vertex AI] Location: {GCP_LOCATION}")
    print(f"{'='*60}\n")
    
    try:
        agent = create_weather_agent()
        log_debug("0. AGENT READY", f"LangChain agent created with Google Vertex AI ({VERTEXAI_MODEL_ID})")
        
        print(f"[Vertex AI] Invoking Vertex AI API...")
        
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
        
        print(f"[Vertex AI] ✓ Received response from Vertex AI")
        print(f"[Vertex AI] Response length: {len(output)} characters")
        
        # Try to extract token usage from messages
        token_info = {"input_tokens": None, "output_tokens": None, "total_tokens": None}
        try:
            for msg in messages:
                if hasattr(msg, 'response_metadata') and msg.response_metadata:
                    usage = msg.response_metadata.get('usage_metadata', {})
                    if usage:
                        token_info["input_tokens"] = usage.get('prompt_token_count') or usage.get('promptTokenCount')
                        token_info["output_tokens"] = usage.get('candidates_token_count') or usage.get('candidatesTokenCount')
                        token_info["total_tokens"] = usage.get('total_token_count') or usage.get('totalTokenCount')
                        
                        if token_info["total_tokens"]:
                            print(f"\n{'='*60}")
                            print(f"📊 TOKEN USAGE:")
                            print(f"   Input tokens:  {token_info['input_tokens']}")
                            print(f"   Output tokens: {token_info['output_tokens']}")
                            print(f"   Total tokens:  {token_info['total_tokens']}")
                            print(f"{'='*60}\n")
                            
                            log_debug("5. TOKENS", f"Input: {token_info['input_tokens']}, Output: {token_info['output_tokens']}, Total: {token_info['total_tokens']}")
                        break
        except Exception as e:
            print(f"[Vertex AI] Could not extract token usage: {e}")
        
        log_debug("5. COMPLETE", "Google Vertex AI agent finished processing")
        
        # Update rate limit timestamp
        last_vertexai_call_time = time.time()
        
        return {
            "response": output,
            "debug": debug_logs,
            "success": True,
            "agent_type": "vertexai",
            "token_usage": token_info if token_info.get("total_tokens") else None
        }
    except Exception as e:
        print(f"[Vertex AI] ✗ Vertex AI API call failed: {str(e)}")
        import traceback
        traceback.print_exc()
        log_debug("ERROR", f"Google Vertex AI agent failed: {str(e)}")
        return {
            "response": f"Agent error: {str(e)}",
            "debug": debug_logs,
            "success": False,
            "agent_type": "vertexai"
        }


def process_without_llm(user_query: str):
    """Fallback: Process query without LLM (direct tool call)"""
    clear_debug()
    log_debug("0. START", f"Processing query (no LLM): {user_query}")
    
    # Extract city from query
    import re
    
    city = None
    
    # Clean the query
    clean_query = user_query.strip().rstrip('?').rstrip('.')
    
    # Pattern 1: "in <city>" at the end - most common pattern
    match = re.search(r'\bin\s+([A-Za-z][A-Za-z\s]*?)$', clean_query, re.IGNORECASE)
    if match:
        city = match.group(1).strip()
    
    # Pattern 2: "for <city>" at the end
    if not city:
        match = re.search(r'\bfor\s+([A-Za-z][A-Za-z\s]*?)$', clean_query, re.IGNORECASE)
        if match:
            city = match.group(1).strip()
    
    # Pattern 3: Just the last word if it looks like a city name (capitalized)
    if not city:
        words = clean_query.split()
        if words:
            last_word = words[-1]
            # Check if it's not a common word
            common_words = {'weather', 'what', 'is', 'the', 'how', 'today', 'now', 'like'}
            if last_word.lower() not in common_words:
                city = last_word
    
    # Default fallback
    if not city:
        city = "Seattle"
    
    # Call weather function directly
    log_debug("1. DIRECT CALL", f"Calling weather function directly for: {city}")
    weather_result = get_weather_data(city)
    
    # Format response
    response = f"""Here's what I found:

{weather_result}

✅ *This data was securely retrieved using Agent Identity authentication!*"""
    
    log_debug("5. COMPLETE", "Query processed (direct mode)")
    
    return {
        "response": response,
        "debug": debug_logs,
        "success": True,
        "agent_type": "direct"
    }


def check_vertexai_available():
    """Check if Google Vertex AI credentials are configured"""
    try:
        from google.cloud import aiplatform
        
        if not GCP_PROJECT_ID:
            print(f"[Vertex AI] ✗ GCP_PROJECT_ID not set")
            return False
        
        print(f"[Vertex AI] Checking Vertex AI availability")
        print(f"[Vertex AI] Project: {GCP_PROJECT_ID}")
        print(f"[Vertex AI] Location: {GCP_LOCATION}")
        
        # Initialize aiplatform
        aiplatform.init(project=GCP_PROJECT_ID, location=GCP_LOCATION)
        
        print(f"[Vertex AI] ✓ Vertex AI initialized successfully")
        return True
    except Exception as e:
        print(f"[Vertex AI] ✗ Vertex AI not available: {e}")
        return False


# ============================================
# Flask Routes
# ============================================
@app.route('/')
def index():
    """Serve the chat UI"""
    return render_template(CHAT_UI_TEMPLATE)


@app.route('/api/chat', methods=['POST'])
def chat():
    """Handle chat messages"""
    data = request.json
    user_message = data.get('message', '')
    use_langchain = data.get('use_langchain', True)
    
    if not user_message:
        return jsonify({"error": "No message provided"}), 400
    
    # Check if LangChain/Vertex AI should be used
    if use_langchain and LANGCHAIN_AVAILABLE and check_vertexai_available():
        result = process_with_langchain(user_message)
    else:
        result = process_without_llm(user_message)
    
    return jsonify(result)


@app.route('/api/status', methods=['GET'])
def status():
    """Check service status"""
    vertexai_ready = check_vertexai_available()
    return jsonify({
        "vertexai_available": vertexai_ready,        "gcp_project": GCP_PROJECT_ID,
        "gcp_location": GCP_LOCATION,        "vertexai_model": VERTEXAI_MODEL_ID,
        "sidecar_url": SIDECAR_URL,
        "agent_app_id": AGENT_APP_ID[:8] + "..." if AGENT_APP_ID else "not set"
    })


@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "service": "LLM Weather Agent (Google Vertex AI)",
        "agent_app_id": AGENT_APP_ID[:8] + "..." if AGENT_APP_ID else "not set"
    })



if __name__ == '__main__':
    print("=" * 60)
    print("  3P Agent Identity Demo - Google Vertex AI Edition")
    print("=" * 60)
    print(f"  Sidecar URL: {SIDECAR_URL}")
    print(f"  Weather API: {WEATHER_API_URL}")
    print(f"  Agent App ID: {AGENT_APP_ID[:8]}..." if AGENT_APP_ID else "  Agent App ID: NOT SET")
    print(f"  GCP Project: {GCP_PROJECT_ID}")
    print(f"  GCP Location: {GCP_LOCATION}")
    print(f"  Vertex AI Model: {VERTEXAI_MODEL_ID}")
    print("=" * 60)
    print("  Open http://localhost:3002 in your browser")
    print("=" * 60)
    
    app.run(host='0.0.0.0', port=3000, debug=True)
