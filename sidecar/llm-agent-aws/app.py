"""
LLM Agent with Weather Tool - Uses LangChain + AWS Bedrock + Agent Identity for secure API calls
This agent demonstrates how an AI agent uses tools with Agent Identity tokens.
"""

import os
import json
import base64
import requests
import time
from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS

# Rate limiting
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
    from langgraph.prebuilt import create_react_agent
    LANGCHAIN_AVAILABLE = True
    print("LangChain with AWS Bedrock loaded successfully")
except ImportError as e:
    print(f"LangChain not fully available: {e}")
    print("Running in direct mode only")

app = Flask(__name__)
CORS(app)

# Configuration
SIDECAR_URL = os.environ.get('SIDECAR_URL', 'http://sidecar:5000')
WEATHER_API_URL = os.environ.get('WEATHER_API_URL', 'http://weather-api:8080')
AGENT_APP_ID = os.environ.get('AGENT_APP_ID', '')
AWS_REGION = os.environ.get('AWS_REGION', 'us-east-1')
BEDROCK_MODEL_ID = os.environ.get('BEDROCK_MODEL_ID', 'anthropic.claude-3-sonnet-20240229-v1:0')

# Store debug info (global for simplicity)
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
    
    # Don't force tool usage - let Claude decide naturally
    # tool_choice="any" was causing loops
    llm_with_tools = llm.bind_tools(tools)
    
    # Use LangGraph ReAct agent
    from langgraph.prebuilt import create_react_agent
    agent = create_react_agent(llm_with_tools, tools)
    
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
    
    clear_debug()
    log_debug("0. START", f"User query: {user_query}")
    log_debug("0. BEDROCK", f"Sending query to AWS Bedrock (model: {BEDROCK_MODEL_ID})")
    
    print(f"\n{'='*60}")
    print(f"[AWS] Processing query with Bedrock LLM")
    print(f"[AWS] Query: {user_query}")
    print(f"[AWS] Model: {BEDROCK_MODEL_ID}")
    print(f"[AWS] Region: {AWS_REGION}")
    print(f"{'='*60}\n")
    
    try:
        agent = create_weather_agent()
        log_debug("0. AGENT READY", f"LangChain agent created with AWS Bedrock ({BEDROCK_MODEL_ID})")
        
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
    return render_template_string(CHAT_UI_TEMPLATE)


@app.route('/api/chat', methods=['POST'])
def chat():
    """Handle chat messages"""
    data = request.json
    user_message = data.get('message', '')
    use_langchain = data.get('use_langchain', True)
    
    if not user_message:
        return jsonify({"error": "No message provided"}), 400
    
    # Check if LangChain/Bedrock should be used
    if use_langchain and LANGCHAIN_AVAILABLE and check_bedrock_available():
        result = process_with_langchain(user_message)
    else:
        result = process_without_llm(user_message)
    
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


@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "service": "LLM Weather Agent (AWS Bedrock)",
        "agent_app_id": AGENT_APP_ID[:8] + "..." if AGENT_APP_ID else "not set"
    })


# ============================================
# HTML Template
# ============================================
CHAT_UI_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>3P Agent Identity Demo - AWS Bedrock</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            min-height: 100vh;
            color: #fff;
        }
        .container {
            max-width: 1400px;
            margin: 0 auto;
            padding: 20px;
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
            height: 100vh;
        }
        .panel {
            background: rgba(255,255,255,0.05);
            border-radius: 16px;
            padding: 20px;
            display: flex;
            flex-direction: column;
        }
        .panel-header {
            display: flex;
            align-items: center;
            gap: 10px;
            margin-bottom: 20px;
            padding-bottom: 15px;
            border-bottom: 1px solid rgba(255,255,255,0.1);
        }
        .panel-header h2 { font-size: 1.2rem; font-weight: 600; }
        .icon {
            width: 32px; height: 32px;
            border-radius: 8px;
            display: flex; align-items: center; justify-content: center;
            font-size: 1.2rem;
        }
        .icon-chat { background: linear-gradient(135deg, #FF9900 0%, #FF6600 100%); }
        .icon-debug { background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); }
        .chat-messages {
            flex: 1; overflow-y: auto;
            padding: 10px; margin-bottom: 20px;
        }
        .message {
            margin-bottom: 15px; padding: 12px 16px;
            border-radius: 12px; max-width: 85%;
        }
        .message.user {
            background: linear-gradient(135deg, #FF9900 0%, #FF6600 100%);
            margin-left: auto;
        }
        .message.assistant {
            background: rgba(255,255,255,0.1);
        }
        .message.assistant pre {
            background: rgba(0,0,0,0.3);
            padding: 10px; border-radius: 8px;
            overflow-x: auto; font-size: 0.85rem;
            margin-top: 10px;
        }
        .input-area {
            display: flex; gap: 10px;
        }
        .input-area input {
            flex: 1; padding: 12px 16px;
            border: none; border-radius: 12px;
            background: rgba(255,255,255,0.1);
            color: #fff; font-size: 1rem;
        }
        .input-area input::placeholder { color: rgba(255,255,255,0.5); }
        .input-area button {
            padding: 12px 24px;
            border: none; border-radius: 12px;
            background: linear-gradient(135deg, #FF9900 0%, #FF6600 100%);
            color: #fff; font-weight: 600;
            cursor: pointer; transition: transform 0.2s;
        }
        .input-area button:hover { transform: scale(1.05); }
        .input-area button:disabled { opacity: 0.5; cursor: not-allowed; }
        .debug-content {
            flex: 1; overflow-y: auto;
            font-family: 'Monaco', 'Menlo', monospace;
            font-size: 0.8rem;
        }
        .debug-entry {
            margin-bottom: 12px; padding: 10px;
            background: rgba(0,0,0,0.3);
            border-radius: 8px;
            border-left: 3px solid #FF9900;
        }
        .debug-entry.error { border-left-color: #f5576c; }
        .debug-entry.success { border-left-color: #2ecc71; }
        .debug-step {
            color: #FF9900; font-weight: 600;
            margin-bottom: 4px;
        }
        .debug-entry.error .debug-step { color: #f5576c; }
        .debug-entry.success .debug-step { color: #2ecc71; }
        .debug-message { color: rgba(255,255,255,0.9); margin-bottom: 6px; }
        .debug-data {
            background: rgba(0,0,0,0.4);
            padding: 8px; border-radius: 4px;
            overflow-x: auto; white-space: pre-wrap;
            color: #a8e6cf; font-size: 0.75rem;
        }
        .status-bar {
            display: flex; gap: 15px;
            padding: 10px; margin-bottom: 10px;
            background: rgba(0,0,0,0.2);
            border-radius: 8px; font-size: 0.8rem;
        }
        .status-item { display: flex; align-items: center; gap: 5px; }
        .status-dot {
            width: 8px; height: 8px;
            border-radius: 50%;
        }
        .status-dot.online { background: #2ecc71; }
        .status-dot.offline { background: #f5576c; }
        .toggle-container {
            display: flex; align-items: center; gap: 10px;
            margin-bottom: 10px; padding: 10px;
            background: rgba(0,0,0,0.2); border-radius: 8px;
        }
        .toggle-switch {
            position: relative;
            width: 50px; height: 26px;
        }
        .toggle-switch input { opacity: 0; width: 0; height: 0; }
        .toggle-slider {
            position: absolute;
            cursor: pointer;
            top: 0; left: 0; right: 0; bottom: 0;
            background-color: #555;
            transition: 0.4s;
            border-radius: 26px;
        }
        .toggle-slider:before {
            position: absolute;
            content: "";
            height: 20px; width: 20px;
            left: 3px; bottom: 3px;
            background-color: white;
            transition: 0.4s;
            border-radius: 50%;
        }
        .toggle-switch input:checked + .toggle-slider { background: linear-gradient(135deg, #FF9900 0%, #FF6600 100%); }
        .toggle-switch input:checked + .toggle-slider:before { transform: translateX(24px); }
        .mode-warning {
            font-size: 0.8em;
            color: #f39c12;
            margin-left: 10px;
        }
        .config-info {
            display: none;
            padding: 10px;
            margin-top: 10px;
            background: rgba(255, 153, 0, 0.1);
            border-radius: 8px;
            border-left: 3px solid #FF9900;
            font-size: 0.8rem;
            line-height: 1.6;
        }
        .config-info.visible { display: block; }
        .config-label { color: #FF9900; font-weight: 600; }
        @media (max-width: 900px) {
            .container { grid-template-columns: 1fr; }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="panel">
            <div class="panel-header">
                <div class="icon icon-chat">🤖</div>
                <h2>3P Agent Identity Demo - AWS Bedrock</h2>
            </div>
            <div class="toggle-container">
                <label class="toggle-switch">
                    <input type="checkbox" id="langchainToggle">
                    <span class="toggle-slider"></span>
                </label>
                <span id="modeLabel">Direct Mode (Skip LLM, call tool directly)</span>
                <span id="modeWarning" class="mode-warning" style="display: none;">⚠️ Requires AWS credentials</span>
            </div>
            <div id="configInfo" class="config-info">
                <span class="config-label">📡 Bedrock Configuration:</span><br>
                <strong>Model:</strong> <span id="modelId">Loading...</span><br>
                <strong>Region:</strong> <span id="awsRegion">Loading...</span><br>
                <strong>Sidecar:</strong> <span id="sidecarUrl">Loading...</span><br>
                <strong>Weather API:</strong> <span id="weatherUrl">Loading...</span>
            </div>
            <div class="status-bar">
                <div class="status-item">
                    <span class="status-dot" id="bedrockStatus"></span>
                    <span>AWS Bedrock</span>
                </div>
                <div class="status-item">
                    <span class="status-dot online"></span>
                    <span>Sidecar</span>
                </div>
                <div class="status-item">
                    <span class="status-dot online"></span>
                    <span>Weather API</span>
                </div>
            </div>
            <div class="chat-messages" id="chatMessages">
                <div class="message assistant">
                    👋 Hi! I'm a weather agent demonstrating Agent Identity tokens with <strong>AWS Bedrock</strong>.
                    <br><br>
                    <strong>Two Modes:</strong><br>
                    <br>
                    <strong>⚡ Direct Mode (Default)</strong> - Fast demo of token flow<br>
                    1. Tool gets Agent Identity token from sidecar<br>
                    2. Tool calls Weather API with token<br>
                    3. API validates token and returns data<br>
                    <br>
                    <strong>🔗 Bedrock Mode</strong> - LLM decides to call tools<br>
                    1. Your question goes to AWS Bedrock LLM<br>
                    2. LLM decides to use the <code>get_weather</code> tool<br>
                    3. Tool gets Agent Identity token from sidecar<br>
                    4. Tool calls Weather API with token<br>
                    5. LLM formats the response<br>
                    <br>
                    <em>Note: Bedrock mode requires AWS credentials configured</em><br>
                    <br>
                    Ask about weather in your favorite city, e.g. "What is weather in Dallas?"
                </div>
            </div>
            <div class="input-area">
                <input type="text" id="userInput" placeholder="Ask about the weather in any city..." />
                <button id="sendBtn" onclick="sendMessage()">Send</button>
            </div>
        </div>
        <div class="panel">
            <div class="panel-header">
                <div class="icon icon-debug">🔍</div>
                <h2>Agent Identity Token Flow</h2>
            </div>
            <div class="debug-content" id="debugContent">
                <div class="debug-entry">
                    <div class="debug-step">Waiting for query...</div>
                    <div class="debug-message">Send a message to see the token flow</div>
                </div>
            </div>
        </div>
    </div>

    <script>
        const chatMessages = document.getElementById('chatMessages');
        const debugContent = document.getElementById('debugContent');
        const userInput = document.getElementById('userInput');
        const sendBtn = document.getElementById('sendBtn');
        const langchainToggle = document.getElementById('langchainToggle');
        const modeLabel = document.getElementById('modeLabel');
        const bedrockStatus = document.getElementById('bedrockStatus');
        const configInfo = document.getElementById('configInfo');
        
        // Check Bedrock status and load config
        async function checkStatus() {
            try {
                const response = await fetch('/api/status');
                const data = await response.json();
                bedrockStatus.className = 'status-dot ' + (data.bedrock_available ? 'online' : 'offline');
                
                // Update config display
                document.getElementById('modelId').textContent = data.bedrock_model || 'N/A';
                document.getElementById('awsRegion').textContent = data.aws_region || 'N/A';
                document.getElementById('sidecarUrl').textContent = data.sidecar_url || 'N/A';
                document.getElementById('weatherUrl').textContent = 'http://weather-api:8080';
                
                if (!data.bedrock_available) {
                    langchainToggle.checked = false;
                    updateModeLabel();
                }
            } catch (e) {
                bedrockStatus.className = 'status-dot offline';
            }
        }
        checkStatus();
        setInterval(checkStatus, 10000);
        
        langchainToggle.addEventListener('change', updateModeLabel);
        function updateModeLabel() {
            const modeWarning = document.getElementById('modeWarning');
            if (langchainToggle.checked) {
                modeLabel.textContent = 'Bedrock Mode (LLM decides to call tools)';
                modeWarning.style.display = 'inline';
                configInfo.classList.add('visible');
            } else {
                modeLabel.textContent = 'Direct Mode (Skip LLM, call tool directly)';
                modeWarning.style.display = 'none';
                configInfo.classList.remove('visible');
            }
        }
        
        function addMessage(content, isUser) {
            const div = document.createElement('div');
            div.className = 'message ' + (isUser ? 'user' : 'assistant');
            div.innerHTML = content.replace(/\\n/g, '<br>');
            chatMessages.appendChild(div);
            chatMessages.scrollTop = chatMessages.scrollHeight;
        }
        
        function updateDebug(debugLogs) {
            debugContent.innerHTML = '';
            debugLogs.forEach(entry => {
                const div = document.createElement('div');
                let entryClass = 'debug-entry';
                if (entry.step.includes('ERROR')) entryClass += ' error';
                if (entry.step.includes('COMPLETE') || entry.step.includes('RECEIVED')) entryClass += ' success';
                div.className = entryClass;
                
                let html = `<div class="debug-step">✅ ${entry.step}</div>`;
                html += `<div class="debug-message">${entry.message}</div>`;
                if (entry.data) {
                    html += `<div class="debug-data">${JSON.stringify(entry.data, null, 2)}</div>`;
                }
                div.innerHTML = html;
                debugContent.appendChild(div);
            });
            debugContent.scrollTop = debugContent.scrollHeight;
        }
        
        async function sendMessage() {
            const message = userInput.value.trim();
            if (!message) return;
            
            addMessage(message, true);
            userInput.value = '';
            sendBtn.disabled = true;
            
            const isLangChain = langchainToggle.checked;
            const processingMsg = isLangChain 
                ? '<div class="debug-entry"><div class="debug-step">⏳ Processing with AWS Bedrock...</div></div>'
                : '<div class="debug-entry"><div class="debug-step">⏳ Processing...</div></div>';
            debugContent.innerHTML = processingMsg;
            
            try {
                const controller = new AbortController();
                const timeoutId = setTimeout(() => controller.abort(), 120000); // 2 minutes for Claude
                
                const response = await fetch('/api/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ 
                        message: message,
                        use_langchain: isLangChain
                    }),
                    signal: controller.signal
                });
                clearTimeout(timeoutId);
                
                const data = await response.json();
                
                let agentInfo = data.agent_type === 'bedrock' 
                    ? '<br><small><em>☁️ Response from AWS Bedrock</em></small>'
                    : '<br><small><em>⚡ Direct tool call (LLM skipped)</em></small>';
                
                addMessage(data.response + agentInfo, false);
                
                if (data.debug) {
                    updateDebug(data.debug);
                }
            } catch (error) {
                if (error.name === 'AbortError') {
                    addMessage('Request timed out. Try Direct Mode for faster results.', false);
                } else {
                    addMessage('Error: ' + error.message, false);
                }
            }
            
            sendBtn.disabled = false;
        }
        
        userInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') sendMessage();
        });
    </script>
</body>
</html>
'''


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
