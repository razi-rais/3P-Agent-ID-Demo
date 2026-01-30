"""
LLM Agent with Weather Tool - Uses LangChain + Agent Identity for secure API calls
This agent demonstrates how an AI agent uses tools with Agent Identity tokens.
"""

import os
import json
import base64
import requests
from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS

# LangChain imports - using try/except for graceful fallback
LANGCHAIN_AVAILABLE = False
ChatOllama = None
tool = None
AgentExecutor = None
create_tool_calling_agent = None
ChatPromptTemplate = None

try:
    from langchain_ollama import ChatOllama
    from langchain_core.tools import tool
    from langchain.agents import AgentExecutor
    from langchain.agents import create_tool_calling_agent
    from langchain_core.prompts import ChatPromptTemplate
    LANGCHAIN_AVAILABLE = True
    print("LangChain loaded successfully")
except ImportError as e:
    # Try alternative import paths for newer LangChain versions
    try:
        from langchain_ollama import ChatOllama
        from langchain_core.tools import tool
        from langchain_core.prompts import ChatPromptTemplate
        from langgraph.prebuilt import create_react_agent
        # Use simpler ReAct agent pattern
        LANGCHAIN_AVAILABLE = "react"
        print("LangChain loaded with ReAct agent")
    except ImportError as e2:
        print(f"LangChain not fully available: {e}")
        print(f"ReAct also failed: {e2}")
        print("Running in direct mode only")

app = Flask(__name__)
CORS(app)

# Configuration
SIDECAR_URL = os.environ.get('SIDECAR_URL', 'http://sidecar:5000')
WEATHER_API_URL = os.environ.get('WEATHER_API_URL', 'http://weather-api:8080')
AGENT_APP_ID = os.environ.get('AGENT_APP_ID', '')
OLLAMA_URL = os.environ.get('OLLAMA_URL', 'http://ollama:11434')
OLLAMA_MODEL = os.environ.get('OLLAMA_MODEL', 'llama3.2')

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
    log_debug("2. TOKEN REQUEST", f"LangChain tool requesting token for Agent: {AGENT_APP_ID}")
    
    try:
        url = f"{SIDECAR_URL}/AuthorizationHeaderUnauthenticated/graph?AgentIdentity={AGENT_APP_ID}"
        log_debug("2. TOKEN REQUEST", f"Sidecar URL: {url}")
        
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
                log_debug("2. TOKEN RECEIVED", "Got Agent Identity token from sidecar", {"jwt_claims": display_claims})
        
        return auth_header
    except Exception as e:
        log_debug("2. TOKEN ERROR", f"Failed to get token: {str(e)}")
        return None


def call_weather_api(city: str, token: str):
    """Call Weather API with Agent Identity token"""
    log_debug("3. WEATHER API", f"Calling Weather API for: {city}")
    
    try:
        url = f"{WEATHER_API_URL}/weather?city={city}"
        headers = {"Authorization": token}
        
        log_debug("3. WEATHER API", f"URL: {url}", {"headers": "Authorization: Bearer <token>"})
        
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        weather_data = response.json()
        log_debug("3. WEATHER RESPONSE", "Got weather data from API", weather_data)
        
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
        Get the current weather for a city. Use this tool when the user asks about weather.
        This tool uses Agent Identity to securely authenticate with the Weather API.
        
        Args:
            city: The name of the city to get weather for (e.g., "Seattle", "New York", "London")
        
        Returns:
            Weather information including temperature, condition, and humidity.
        """
        return get_weather_data(city)


# ============================================
# LangChain Agent Setup
# ============================================
def create_weather_agent():
    """Create LangChain agent with weather tool"""
    
    # Initialize Ollama LLM with extended timeout for first request
    llm = ChatOllama(
        model=OLLAMA_MODEL,
        base_url=OLLAMA_URL,
        temperature=0.7,
        timeout=120,  # 2 min timeout for first request (model loading)
    )
    
    # Define tools
    tools = [get_weather]
    
    if LANGCHAIN_AVAILABLE == "react":
        # Use LangGraph ReAct agent
        from langgraph.prebuilt import create_react_agent
        agent = create_react_agent(llm, tools)
        return agent
    else:
        # Use traditional AgentExecutor
        prompt = ChatPromptTemplate.from_messages([
            ("system", """You are a helpful weather assistant. When users ask about weather, 
use the get_weather tool to fetch real weather data. The tool uses Agent Identity 
authentication to securely access the weather API.

Always use the tool for weather queries - don't make up weather data.
After getting weather data, provide a friendly, conversational response."""),
            ("human", "{input}"),
            ("placeholder", "{agent_scratchpad}"),
        ])
        
        agent = create_tool_calling_agent(llm, tools, prompt)
        
        agent_executor = AgentExecutor(
            agent=agent,
            tools=tools,
            verbose=True,
            handle_parsing_errors=True,
            max_iterations=3
        )
        
        return agent_executor


def process_with_langchain(user_query: str):
    """Process query using LangChain agent with tools"""
    clear_debug()
    log_debug("0. START", f"User query: {user_query}")
    log_debug("0. LANGCHAIN", f"Sending query to LangChain agent (mode: {LANGCHAIN_AVAILABLE})")
    
    try:
        agent = create_weather_agent()
        log_debug("0. AGENT READY", f"LangChain agent created with Ollama ({OLLAMA_MODEL})")
        
        if LANGCHAIN_AVAILABLE == "react":
            # LangGraph ReAct agent uses different interface
            result = agent.invoke({"messages": [("human", user_query)]})
            # Extract final message
            output = result.get("messages", [])[-1].content if result.get("messages") else "No response"
        else:
            result = agent.invoke({"input": user_query})
            output = result.get("output", "No response from agent")
        
        log_debug("5. COMPLETE", "LangChain agent finished processing")
        
        return {
            "response": output,
            "debug": debug_logs,
            "success": True,
            "agent_type": "langchain"
        }
    except Exception as e:
        log_debug("ERROR", f"LangChain agent failed: {str(e)}")
        return {
            "response": f"Agent error: {str(e)}",
            "debug": debug_logs,
            "success": False,
            "agent_type": "langchain"
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


def check_ollama_available():
    """Check if Ollama is running and has the model"""
    try:
        response = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        if response.status_code == 200:
            models = response.json().get("models", [])
            model_names = [m.get("name", "").split(":")[0] for m in models]
            return OLLAMA_MODEL.split(":")[0] in model_names
    except:
        pass
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
    
    # Check if LangChain/Ollama should be used
    if use_langchain and LANGCHAIN_AVAILABLE and check_ollama_available():
        result = process_with_langchain(user_message)
    else:
        result = process_without_llm(user_message)
    
    return jsonify(result)


@app.route('/api/status', methods=['GET'])
def status():
    """Check service status"""
    ollama_ready = check_ollama_available()
    return jsonify({
        "ollama_available": ollama_ready,
        "ollama_url": OLLAMA_URL,
        "ollama_model": OLLAMA_MODEL,
        "sidecar_url": SIDECAR_URL,
        "agent_app_id": AGENT_APP_ID[:8] + "..." if AGENT_APP_ID else "not set"
    })


@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "service": "LLM Weather Agent (LangChain)",
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
    <title>3P Agent Identity Demo</title>
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
        .icon-chat { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); }
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
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
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
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
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
            border-left: 3px solid #667eea;
        }
        .debug-entry.error { border-left-color: #f5576c; }
        .debug-entry.success { border-left-color: #2ecc71; }
        .debug-step {
            color: #667eea; font-weight: 600;
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
        .toggle-switch input:checked + .toggle-slider { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); }
        .toggle-switch input:checked + .toggle-slider:before { transform: translateX(24px); }
        .mode-warning {
            font-size: 0.8em;
            color: #f39c12;
            margin-left: 10px;
        }
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
                <h2>3P Agent Identity Demo</h2>
            </div>
            <div class="toggle-container">
                <label class="toggle-switch">
                    <input type="checkbox" id="langchainToggle">
                    <span class="toggle-slider"></span>
                </label>
                <span id="modeLabel">Direct Mode (Skip LLM, call tool directly)</span>
                <span id="modeWarning" class="mode-warning" style="display: none;">⚠️ First LLM request may take 30-60s</span>
            </div>
            <div class="status-bar">
                <div class="status-item">
                    <span class="status-dot" id="ollamaStatus"></span>
                    <span>Ollama</span>
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
                    👋 Hi! I'm a weather agent demonstrating Agent Identity tokens.
                    <br><br>
                    <strong>Two Modes:</strong><br>
                    <br>
                    <strong>⚡ Direct Mode (Default)</strong> - Fast demo of token flow<br>
                    1. Tool gets Agent Identity token from sidecar<br>
                    2. Tool calls Weather API with token<br>
                    3. API validates token and returns data<br>
                    <br>
                    <strong>🔗 LangChain Mode</strong> - LLM decides to call tools<br>
                    1. Your question goes to the LLM (Ollama)<br>
                    2. LLM decides to use the <code>get_weather</code> tool<br>
                    3. Tool gets Agent Identity token from sidecar<br>
                    4. Tool calls Weather API with token<br>
                    5. LLM formats the response<br>
                    <br>
                    <em>Note: First LangChain request may take 30-60s (model loading)</em><br>
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
        const ollamaStatus = document.getElementById('ollamaStatus');
        
        // Check Ollama status
        async function checkStatus() {
            try {
                const response = await fetch('/api/status');
                const data = await response.json();
                ollamaStatus.className = 'status-dot ' + (data.ollama_available ? 'online' : 'offline');
                if (!data.ollama_available) {
                    langchainToggle.checked = false;
                    updateModeLabel();
                }
            } catch (e) {
                ollamaStatus.className = 'status-dot offline';
            }
        }
        checkStatus();
        setInterval(checkStatus, 10000);
        
        langchainToggle.addEventListener('change', updateModeLabel);
        function updateModeLabel() {
            const modeWarning = document.getElementById('modeWarning');
            if (langchainToggle.checked) {
                modeLabel.textContent = 'LangChain Mode (LLM decides to call tools)';
                modeWarning.style.display = 'inline';
            } else {
                modeLabel.textContent = 'Direct Mode (Skip LLM, call tool directly)';
                modeWarning.style.display = 'none';
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
                ? '<div class="debug-entry"><div class="debug-step">⏳ Processing with LangChain...</div><div class="debug-message">Waiting for Ollama LLM (first request may take 30-60s)</div></div>'
                : '<div class="debug-entry"><div class="debug-step">⏳ Processing...</div></div>';
            debugContent.innerHTML = processingMsg;
            
            try {
                const controller = new AbortController();
                const timeoutId = setTimeout(() => controller.abort(), isLangChain ? 120000 : 30000);
                
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
                
                let agentInfo = data.agent_type === 'langchain' 
                    ? '<br><small><em>🔗 Response from LangChain Agent</em></small>'
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
    print("  3P Agent Identity Demo")
    print("=" * 60)
    print(f"  Sidecar URL: {SIDECAR_URL}")
    print(f"  Weather API: {WEATHER_API_URL}")
    print(f"  Agent App ID: {AGENT_APP_ID[:8]}..." if AGENT_APP_ID else "  Agent App ID: NOT SET")
    print(f"  Ollama URL: {OLLAMA_URL}")
    print(f"  Ollama Model: {OLLAMA_MODEL}")
    print("=" * 60)
    print("  Open http://localhost:3000 in your browser")
    print("=" * 60)
    
    app.run(host='0.0.0.0', port=3000, debug=True)
