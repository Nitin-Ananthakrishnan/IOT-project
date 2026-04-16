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
    
    # A. LISTEN FOR COMMANDS FROM CLOUD (RESUME/STOP)
    if msg.topic == MQTT_TOPIC_ACTUATORS:
        cmd = msg.payload.decode()
        if cmd == "RESUME":
            with state_lock:
                halted = False
                software_trip = False
                last_sent_speed = -1 
        elif cmd == "STOP":
            with state_lock:
                halted = True
        return

    # B. PROCESS SENSOR DATA
    try:
        payload = json.loads(msg.payload.decode())
        t = engine.analyze(payload)
        d = payload["data"]; h = payload["health"]
        
        with state_lock:
            if d.get("motor_mA", 0) > MAX_ALLOWED_MA: software_trip = True

            if software_trip or halted or h.get("trip_status", False):
                if software_trip: mode_str = "FAULT: OVERCURRENT"
                elif halted: mode_str = "MANUAL HALT"
                else: mode_str = "HARDWARE TRIP"
                current_speed, s1, s2 = 0, 0, 180
                client.publish(MQTT_TOPIC_ACTUATORS, "STOP")
            else:
                mode_str = "AUTONOMOUS"
                aqi = max(400, min(1200, d.get("air_qual_raw", 400)))
                ratio = (aqi - 400) / 800
                current_speed = int(ratio * (100 - 40) + 40)
                s1 = int(ratio * 180); s2 = 180 - s1
                
                if (abs(current_speed - last_sent_speed) > 2):
                    client.publish(MQTT_TOPIC_ACTUATORS, f"{current_speed},{s1},{s2}")
                    last_sent_speed, last_sent_s1, last_sent_s2 = current_speed, s1, s2

        # C. RE-PUBLISH FOR CLOUD & UI
        payload["health_score"] = t["health"]
        payload["twin_expected_t"] = t["exp_t"]
        payload["ml_prediction"] = ml_prediction
        payload["health"]["sys_status"] = mode_str
        payload["health"]["software_trip"] = software_trip
        client.publish("telemetry/room1/processed", json.dumps(payload))
        
        # D. MASTER DASHBOARD RENDERING
        os.system('cls' if os.name == 'nt' else 'clear')
        print("="*60)
        print("         HVAC EDGE GATEWAY - MASTER DASHBOARD         ")
        print("="*60)
        
        # System State
        status_col = "\033[91m" if "FAULT" in mode_str or "HALT" in mode_str else "\033[92m"
        print(f"SYSTEM MODE   : {status_col}{mode_str}\033[0m")
        print(f"HEALTH SCORE  : {t['health']}%")
        print(f"AI PREDICTION : \033[93m{ml_prediction}\033[0m")
        print("-" * 60)
        
        # Motor Metrics
        print(f"MOTOR TELEMETRY:")
        print(f"  - Real Load      : {d['motor_mA']:.1f} mA")
        print(f"  - Real Temp      : {d['motor_temp_C']:.2f} °C")
        print(f"  - Twin Expected  : {t['exp_t']:.2f} °C")
        print(f"  - Deviation      : {t['dev']:.2f} °C")
        print("-" * 60)
        
        # Environment Metrics
        print(f"ENVIRONMENTAL DATA:")
        print(f"  - Room Temp      : {d['env_temp_C']:.2f} °C")
        print(f"  - Humidity       : {d['env_hum_RH']:.1f} %")
        print(f"  - Pressure       : {d['env_pres_hPa']:.1f} hPa")
        print(f"  - Air Qual (Raw) : {d['air_qual_raw']}")
        print("-" * 60)
        
        # Actuator Metrics
        print(f"ACTUATOR STATES:")
        print(f"  - Fan PWM Speed  : {last_sent_speed}%")
        print(f"  - Servo 1 (Duct) : {last_sent_s1}°")
        print(f"  - Servo 2 (Exh)  : {last_sent_s2}°")
        print("="*60)
        print("Controls: 's' to STOP | 'r' to RESUME")

    except Exception as e: print(f"Logic Error: {e}")

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