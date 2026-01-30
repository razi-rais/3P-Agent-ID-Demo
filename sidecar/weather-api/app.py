"""
Weather API - Validates Agent Identity Tokens
This API calls real weather data and requires valid Agent ID tokens.
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import jwt
import requests
from datetime import datetime
from functools import wraps

app = Flask(__name__)
CORS(app)

# City coordinates for Open-Meteo API (lat, lon)
CITY_COORDS = {
    "seattle": (47.6062, -122.3321),
    "new york": (40.7128, -74.0060),
    "los angeles": (33.9425, -118.4081),
    "chicago": (41.8781, -87.6298),
    "miami": (25.7617, -80.1918),
    "denver": (39.7392, -104.9903),
    "san francisco": (37.7749, -122.4194),
    "boston": (42.3601, -71.0589),
    "austin": (30.2672, -97.7431),
    "portland": (45.5152, -122.6784),
    "dallas": (32.7767, -96.7970),
    "houston": (29.7604, -95.3698),
    "phoenix": (33.4484, -112.0740),
    "atlanta": (33.7490, -84.3880),
    "london": (51.5074, -0.1278),
    "paris": (48.8566, 2.3522),
    "tokyo": (35.6762, 139.6503),
    "sydney": (-33.8688, 151.2093),
    "toronto": (43.6532, -79.3832),
    "berlin": (52.5200, 13.4050),
    "mumbai": (19.0760, 72.8777),
    "dubai": (25.2048, 55.2708),
    "singapore": (1.3521, 103.8198),
}

# Weather code to condition mapping (WMO codes)
WEATHER_CODES = {
    0: "Clear Sky",
    1: "Mainly Clear",
    2: "Partly Cloudy",
    3: "Overcast",
    45: "Foggy",
    48: "Depositing Rime Fog",
    51: "Light Drizzle",
    53: "Moderate Drizzle",
    55: "Dense Drizzle",
    61: "Slight Rain",
    63: "Moderate Rain",
    65: "Heavy Rain",
    71: "Slight Snow",
    73: "Moderate Snow",
    75: "Heavy Snow",
    80: "Slight Rain Showers",
    81: "Moderate Rain Showers",
    82: "Violent Rain Showers",
    95: "Thunderstorm",
    96: "Thunderstorm with Hail",
    99: "Thunderstorm with Heavy Hail",
}


def get_real_weather(city: str):
    """Get real weather from Open-Meteo API (free, no API key needed)"""
    city_lower = city.lower()
    
    # Get coordinates
    if city_lower in CITY_COORDS:
        lat, lon = CITY_COORDS[city_lower]
    else:
        # Try geocoding API for unknown cities
        try:
            geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={city}&count=1"
            geo_resp = requests.get(geo_url, timeout=5)
            geo_data = geo_resp.json()
            if geo_data.get("results"):
                lat = geo_data["results"][0]["latitude"]
                lon = geo_data["results"][0]["longitude"]
                city = geo_data["results"][0]["name"]
            else:
                return None, f"City '{city}' not found"
        except Exception as e:
            return None, f"Geocoding failed: {str(e)}"
    
    # Get weather from Open-Meteo
    try:
        weather_url = (
            f"https://api.open-meteo.com/v1/forecast?"
            f"latitude={lat}&longitude={lon}"
            f"&current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m"
            f"&temperature_unit=fahrenheit"
            f"&timezone=auto"
        )
        resp = requests.get(weather_url, timeout=10)
        data = resp.json()
        
        current = data.get("current", {})
        weather_code = current.get("weather_code", 0)
        
        return {
            "temperature": round(current.get("temperature_2m", 0)),
            "humidity": round(current.get("relative_humidity_2m", 0)),
            "condition": WEATHER_CODES.get(weather_code, "Unknown"),
            "wind_speed": round(current.get("wind_speed_10m", 0)),
            "timestamp": current.get("time", datetime.utcnow().isoformat()),
            "timezone": data.get("timezone", "UTC"),
        }, None
        
    except Exception as e:
        return None, f"Weather API failed: {str(e)}"


def validate_token(f):
    """Decorator to validate Agent Identity tokens"""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization', '')
        
        if not auth_header:
            return jsonify({
                "error": "Missing Authorization header",
                "message": "Please provide a valid Agent Identity token"
            }), 401
        
        if not auth_header.startswith('Bearer '):
            return jsonify({
                "error": "Invalid Authorization format",
                "message": "Use 'Bearer <token>' format"
            }), 401
        
        token = auth_header.replace('Bearer ', '')
        
        try:
            # Decode without verification (for demo purposes)
            # In production, you would verify the signature
            unverified = jwt.decode(token, options={"verify_signature": False})
            
            # Check for Agent Identity claim
            xms_frd = unverified.get('xms_frd', '')
            
            # Store token info in request for logging
            request.token_claims = {
                "appid": unverified.get('appid', 'unknown'),
                "aud": unverified.get('aud', 'unknown'),
                "roles": unverified.get('roles', []),
                "xms_frd": xms_frd,
                "is_agent_identity": xms_frd == "FederatedAgent"
            }
            
            print(f"[TOKEN VALIDATED] App ID: {request.token_claims['appid']}, Is Agent: {request.token_claims['is_agent_identity']}")
            
        except jwt.exceptions.DecodeError as e:
            return jsonify({
                "error": "Invalid token format",
                "message": str(e)
            }), 401
        except Exception as e:
            return jsonify({
                "error": "Token validation failed",
                "message": str(e)
            }), 401
        
        return f(*args, **kwargs)
    return decorated


@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint (no auth required)"""
    return jsonify({"status": "healthy", "service": "Weather API"})


@app.route('/weather', methods=['GET'])
@validate_token
def get_weather():
    """Get real-time weather for any city - requires valid Agent Identity token"""
    city = request.args.get('city', 'seattle')
    
    # Get real weather data
    weather, error = get_real_weather(city)
    
    if error:
        return jsonify({
            "error": error,
            "city": city,
            "validated_by": "Agent Identity Token",
            "agent_app_id": request.token_claims.get("appid", "unknown"),
        }), 404
    
    response = {
        "city": city.title(),
        "temperature": weather["temperature"],
        "temperature_unit": "F",
        "condition": weather["condition"],
        "humidity": weather["humidity"],
        "humidity_unit": "%",
        "wind_speed": weather["wind_speed"],
        "wind_unit": "mph",
        "timestamp": weather["timestamp"],
        "timezone": weather["timezone"],
        "validated_by": "Agent Identity Token",
        "agent_app_id": request.token_claims.get("appid", "unknown"),
        "is_agent_identity": request.token_claims.get("is_agent_identity", False),
        "data_source": "Open-Meteo API (Real-time)"
    }
    
    print(f"[WEATHER REQUEST] City: {city.title()}, Temp: {weather['temperature']}°F, Agent: {response['agent_app_id']}")
    
    return jsonify(response)


@app.route('/weather/forecast', methods=['GET'])
@validate_token
def get_forecast():
    """Get 5-day forecast - requires valid Agent Identity token"""
    city = request.args.get('city', 'seattle').lower()
    
    base_weather = WEATHER_DATA.get(city, {"temp": 55, "condition": "Cloudy", "humidity": 60})
    
    forecast = []
    conditions = ["Sunny", "Partly Cloudy", "Cloudy", "Rainy", "Windy"]
    
    for i in range(5):
        forecast.append({
            "day": i + 1,
            "high": base_weather["temp"] + random.randint(-5, 10),
            "low": base_weather["temp"] - random.randint(5, 15),
            "condition": random.choice(conditions),
            "precipitation_chance": random.randint(0, 100)
        })
    
    return jsonify({
        "city": city.title(),
        "forecast": forecast,
        "validated_by": "Agent Identity Token",
        "agent_app_id": request.token_claims.get("appid", "unknown")
    })


if __name__ == '__main__':
    print("=" * 60)
    print("  Weather API - Agent Identity Token Validation Demo")
    print("=" * 60)
    print("Endpoints:")
    print("  GET /health         - Health check (no auth)")
    print("  GET /weather?city=X - Get weather (requires Agent ID token)")
    print("  GET /weather/forecast?city=X - Get forecast (requires Agent ID token)")
    print("=" * 60)
    app.run(host='0.0.0.0', port=8080, debug=True)
