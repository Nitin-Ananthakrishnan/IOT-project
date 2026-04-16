import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion
import json, time, os, sqlite3, threading
from datetime import datetime
import pandas as pd
from sklearn.linear_model import LinearRegression

# ==========================================
# --- CONFIGURATION ---
# ==========================================
MQTT_BROKER = "10.93.21.157"  # FUJITSU IP
MQTT_TOPIC_SENSORS = "telemetry/room1/sensors"
MQTT_TOPIC_ACTUATORS = "command/room1/actuators"
MQTT_USER = "hvac_admin"
MQTT_PASS = "iot_secure_123"

MAX_ALLOWED_MA = 165.0
CRITICAL_TEMP = 80.0

# --- THREAD-SAFE GLOBALS ---
state_lock = threading.Lock()
halted = False
software_trip = False
ml_prediction = "Learning..."
last_sent_speed, last_sent_s1, last_sent_s2 = 0, 0, 180

# ==========================================
# --- 1. DIGITAL TWIN MATH ENGINE ---
# ==========================================
class DigitalTwinEngine:
    def __init__(self):
        self.heat_coeff = 0.001 
        self.smooth_health = 100.0
        self.alpha = 0.1 

    def analyze(self, data):
        try:
            env_t = data["data"]["env_temp_C"]
            real_t = data["data"]["motor_temp_C"]
            mA = data["data"].get("motor_mA", 0)
            exp_t = env_t + (mA * self.heat_coeff)
            deviation = abs(real_t - exp_t)
            instant_health = max(0, min(100, 100 - (deviation * 10)))
            self.smooth_health = (self.smooth_health * 0.9) + (instant_health * self.alpha)
            return {"exp_t": round(exp_t, 2), "health": round(self.smooth_health, 1), "dev": round(deviation,2)}
        except: return {"exp_t": 25.0, "health": 100, "dev": 0}

engine = DigitalTwinEngine()

# ==========================================
# --- 2. MQTT MESSAGE HANDLER ---
# ==========================================
def on_message(client, userdata, msg):
    global halted, software_trip, last_sent_speed, last_sent_s1, last_sent_s2, ml_prediction
    
    # 1. LISTEN FOR OVERRIDES FROM CLOUD (STOP/RESUME)
    if msg.topic == MQTT_TOPIC_ACTUATORS:
        cmd = msg.payload.decode()
        if cmd == "RESUME":
            with state_lock:
                halted = False
                software_trip = False
                last_sent_speed = -1 # Force a logic refresh
                print("\n>>> REMOTE RESUME RECEIVED")
        elif cmd == "STOP":
            with state_lock:
                halted = True
                print("\n>>> REMOTE STOP RECEIVED")
        return

    # 2. PROCESS INCOMING SENSOR TELEMETRY
    try:
        payload = json.loads(msg.payload.decode())
        t_analysis = engine.analyze(payload)
        d = payload["data"]
        h = payload["health"]
        
        with state_lock:
            # --- SAFETY CHECK: 200mA SOFTWARE TRIP ---
            if d.get("motor_mA", 0) > MAX_ALLOWED_MA:
                software_trip = True

            # --- HIERARCHICAL CONTROL LOGIC ---
            if software_trip or halted or h.get("trip_status", False):
                if software_trip: mode_str = "FAULT: OVERCURRENT"
                elif halted: mode_str = "MANUAL HALT"
                else: mode_str = "HARDWARE TRIP"
                
                current_speed, s1, s2 = 0, 0, 180
                client.publish(MQTT_TOPIC_ACTUATORS, "STOP")
            
            else:
                # --- AUTONOMOUS FUSION LOGIC ---
                mode_str = "AUTONOMOUS (FUSED)"
                
                # A. Air Quality Demand (400 to 1200 range)
                aqi = d.get("air_qual_raw", 400)
                aqi_ratio = (max(400, min(1200, aqi)) - 400) / 800.0
                
                # B. Temperature Demand (22C to 30C range)
                room_t = d.get("env_temp_C", 24.0)
                temp_ratio = (max(22.0, min(30.0, room_t)) - 22.0) / 8.0
                
                # C. Max-Selection: Use whichever demand is higher
                final_ratio = max(aqi_ratio, temp_ratio)
                
                # Map to physical actuator limits
                current_speed = int(final_ratio * (100 - 40) + 40) # 40% to 100%
                s1 = int(final_ratio * 180)                       # 0 to 180 deg
                s2 = 180 - s1                                     # Inverse Duct
                
                # --- VIBRATION KILLER (Delta Check) ---
                # Only publish to ESP8266 if values changed by more than 2 units
                if (abs(current_speed - last_sent_speed) > 2 or abs(s1 - last_sent_s1) > 2):
                    client.publish(MQTT_TOPIC_ACTUATORS, f"{current_speed},{s1},{s2}")
                    last_sent_speed, last_sent_s1, last_sent_s2 = current_speed, s1, s2

        # 3. ENRICH & RE-PUBLISH (For Cloud Database and 3D UI)
        payload["health_score"] = t_analysis["health"]
        payload["twin_expected_t"] = t_analysis["exp_t"]
        payload["ml_prediction"] = ml_prediction
        payload["health"]["sys_status"] = mode_str
        payload["health"]["software_trip"] = software_trip
        client.publish("telemetry/room1/processed", json.dumps(payload))
        
        # 4. RENDER MASTER TERMINAL DASHBOARD
        os.system('cls' if os.name == 'nt' else 'clear')
        print("="*65)
        print("         HVAC EDGE GATEWAY - MISSION CONTROL         ")
        print("="*65)
        
        # System State
        status_col = "\033[91m" if "FAULT" in mode_str or "HALT" in mode_str else "\033[92m"
        print(f"SYSTEM MODE   : {status_col}{mode_str}\033[0m")
        print(f"HEALTH SCORE  : {t_analysis['health']}%")
        print(f"AI PREDICTION : \033[93m{ml_prediction}\033[0m")
        print("-" * 65)
        
        # Sensor Fusion Display
        print(f"ENVIRONMENT SENSORS:")
        print(f"  - Room Temp     : {d['env_temp_C']:.2f} °C (Demand: {int(temp_ratio*100)}%)")
        print(f"  - Air Qual (Raw): {d['air_qual_raw']} (Demand: {int(aqi_ratio*100)}%)")
        print(f"  - Humidity      : {d['env_hum_RH']:.1f} %")
        print("-" * 65)
        
        # Digital Twin Math
        print(f"DIGITAL TWIN (MOTOR HEALTH):")
        print(f"  - Real Load     : {d.get('motor_mA', 0):.1f} mA")
        print(f"  - Real Temp     : {d['motor_temp_C']:.2f} °C")
        print(f"  - Twin Expected : {t_analysis['exp_t']:.2f} °C")
        print(f"  - Deviation     : {t_analysis['dev']:.2f} °C")
        print("-" * 65)
        
        # Physical Command Confirmation
        print(f"ACTUATOR OUTPUTS:")
        print(f"  - Fan Speed     : {last_sent_speed}%")
        print(f"  - Intake Duct   : {last_sent_s1}°")
        print(f"  - Exhaust Duct  : {last_sent_s2}°")
        print("="*65)
        print("COMMANDS: 's'+Enter (HALT) | 'r'+Enter (RESUME/RESET)")

    except Exception as e:
        print(f"Gateway Logic Error: {e}")

# ==========================================
# --- 3. BACKGROUND WORKERS ---
# ==========================================
def user_input_thread(client):
    global halted, software_trip
    while True:
        cmd = input().strip().lower()
        with state_lock:
            if cmd == 's':
                halted = True
                client.publish(MQTT_TOPIC_ACTUATORS, "STOP")
            elif cmd == 'r':
                halted = False
                software_trip = False
                client.publish(MQTT_TOPIC_ACTUATORS, "RESUME")

# Use the same ML analysis function as before
def run_ml_analysis():
    global ml_prediction
    while True:
        try:
            time.sleep(15)
            # Accessing DB from Dell (Ensure it creates its own local log for ML)
            conn = sqlite3.connect("local_cache.db")
            conn.execute('CREATE TABLE IF NOT EXISTS hvac_logs (timestamp, real_temp)')
            df = pd.read_sql_query("SELECT * FROM hvac_logs ORDER BY timestamp DESC LIMIT 100", conn)
            conn.close()
            # ... [Rest of ML math from previous scripts] ...
        except: pass

# ==========================================
# --- 4. MAIN STARTUP ---
# ==========================================
if __name__ == "__main__":
    edge_client = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION2)
    edge_client.username_pw_set(MQTT_USER, MQTT_PASS)
    edge_client.on_message = on_message
    
    edge_client.connect(MQTT_BROKER, 1883, 60)
    edge_client.subscribe([(MQTT_TOPIC_SENSORS, 0), (MQTT_TOPIC_ACTUATORS, 0)])
    edge_client.loop_start()
    
    threading.Thread(target=user_input_thread, args=(edge_client,), daemon=True).start()
    
    while True:
        time.sleep(1)