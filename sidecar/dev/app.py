"""
LLM Agent with Weather Tool - Uses LangChain + Agent Identity for secure API calls
This agent demonstrates how an AI agent uses tools with Agent Identity tokens.
"""

import os
import json
import base64
import requests
from flask import Flask, request, jsonify, render_template
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
    return render_template(CHAT_UI_TEMPLATE)


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
