import multiprocessing
import pybullet as p
import pybullet_data
import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion
import json
import time
import os
import sqlite3
from datetime import datetime

# ==========================================
# --- CONFIGURATION ---
# ==========================================
MQTT_BROKER_HOST = "localhost" # Ensure this matches your Mosquitto IP
MQTT_TOPIC = "telemetry/room1/sensors"
DB_FILE = "hvac_digital_twin.db"

class DigitalTwinEngine:
    def __init__(self):
        self.heat_coeff = 0.005 
    def analyze(self, data):
        try:
            env_t = data["data"]["env_temp_C"]
            real_t = data["data"]["motor_temp_C"]
            mA = data["data"]["motor_mA"]
            exp_t = env_t + (mA * self.heat_coeff)
            health = max(0, min(100, 100 - (abs(real_t - exp_t) * 10)))
            return {"exp_t": round(exp_t, 2), "health": round(health, 1)}
        except: return {"exp_t": 25.0, "health": 100}

# ==========================================
# --- WINDOW 1: HVAC SYSTEM ---
# ==========================================
def run_hvac_window():
    p.connect(p.GUI)
    p.configureDebugVisualizer(p.COV_ENABLE_GUI, 0)
    p.setAdditionalSearchPath(pybullet_data.getDataPath())
    
    def create_box(pos, size, color):
        vis = p.createVisualShape(p.GEOM_BOX, halfExtents=size, rgbaColor=color)
        return p.createMultiBody(0, -1, vis, basePosition=pos)

    def create_5_walled_layer(z_pos):
        create_box([0, 1.0, z_pos], [2.5, 0.1, 0.5], [0.5, 0.5, 0.5, 1])
        create_box([-2.5, 0, z_pos], [0.1, 1.0, 0.5], [0.5, 0.5, 0.5, 1])
        create_box([2.5, 0, z_pos], [0.1, 1.0, 0.5], [0.5, 0.5, 0.5, 1])
        create_box([0, 0, z_pos + 0.5], [2.6, 1.1, 0.1], [0.4, 0.4, 0.4, 1]) 
        create_box([0, 0, z_pos - 0.5], [2.6, 1.1, 0.1], [0.4, 0.4, 0.4, 1])

    create_5_walled_layer(2.5) 
    create_5_walled_layer(0.5) 
    door_id = create_box([0.5, 0, 1.5], [0.15, 0.1, 0.5], [0.5, 0.5, 0.5, 1])

    text_items = []
    local_data = None
    engine = DigitalTwinEngine()

    def on_msg(client, userdata, msg):
        nonlocal local_data
        try:
            local_data = json.loads(msg.payload.decode())
            print("Window 1 (HVAC) received data!")
        except: pass

    cli = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION2)
    cli.on_message = on_msg
    cli.connect(MQTT_BROKER_HOST, 1883)
    cli.subscribe(MQTT_TOPIC)
    cli.loop_start()
    
    p.resetDebugVisualizerCamera(5, 0, -20, [0, 0, 1.5])

    while True:
        # Clear old text
        for item in text_items: p.removeUserDebugItem(item)
        text_items.clear()

        if local_data:
            d = local_data["data"]
            h = local_data["health"]
            t = engine.analyze(local_data)

            # Labels
            status_top = "ON" if d["motor_mA"] > 10 else "OFF"
            col_top = [0, 1, 0] if status_top == "ON" else [1, 0, 0]
            text_items.append(p.addUserDebugText(f"REAL MOTOR: {d['motor_temp_C']} C", [-2.5, 0, 3.5], textColorRGB=col_top, textSize=1.5))
            text_items.append(p.addUserDebugText(f"TWIN MOTOR: {t['exp_t']} C", [-2.5, 0, 1.0], textColorRGB=[0, 1, 1], textSize=1.5))
            
            # Door
            if door_id: p.removeBody(door_id)
            is_open = not h["trip_status"]
            door_col = [0, 0.8, 0, 1] if is_open else [0.8, 0, 0, 1]
            door_id = create_box([0.5, 0, 1.5], [0.15, 0.1, 0.5], door_col)
            text_items.append(p.addUserDebugText("VENT OPEN" if is_open else "VENT CLOSED", [0.5, 0, 2.2], textColorRGB=door_col, textSize=1.5))
        else:
            text_items.append(p.addUserDebugText("Waiting for ESP32 Data...", [-1, 0, 2], textColorRGB=[1,1,1], textSize=1.2))

        p.stepSimulation()
        time.sleep(0.1)

# ==========================================
# --- WINDOW 2: ROOM BALLROOM ---
# ==========================================
def run_room_window():
    p.connect(p.GUI)
    p.configureDebugVisualizer(p.COV_ENABLE_GUI, 0)
    p.setAdditionalSearchPath(pybullet_data.getDataPath())
    
    def create_colored_box(pos, size, color):
        vis = p.createVisualShape(p.GEOM_BOX, halfExtents=size, rgbaColor=color)
        p.createMultiBody(0, -1, vis, basePosition=pos)

    create_colored_box([0, 0, -0.1], [5, 5, 0.1], [0.2, 0.2, 0.2, 1])
    wall_color = [0.2, 0.4, 0.6, 1]
    create_colored_box([0, 5, 1], [5, 0.1, 1], wall_color)
    create_colored_box([0, -5, 1], [5, 0.1, 1], wall_color)
    create_colored_box([5, 0, 1], [0.1, 5, 1], wall_color)
    create_colored_box([-5, 0, 1], [0.1, 5, 1], wall_color)
    
    p.resetDebugVisualizerCamera(10, 45, -30, [0,0,1])

    text_id = -1
    local_data = None

    def on_msg(client, userdata, msg):
        nonlocal local_data
        try:
            local_data = json.loads(msg.payload.decode())
            print("Window 2 (Room) received data!")
        except: pass

    cli = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION2)
    cli.on_message = on_msg
    cli.connect(MQTT_BROKER_HOST, 1883)
    cli.subscribe(MQTT_TOPIC)
    cli.loop_start()

    while True:
        if local_data:
            d = local_data["data"]
            s = local_data["health"]["sys_status"]
            info = (f"--- HVAC SYSTEM STATUS: {s} ---\n"
                    f"Room Temp: {d['env_temp_C']} C\n"
                    f"Humidity:  {d['env_hum_RH']} %\n"
                    f"Pressure:  {d['env_pres_hPa']} hPa\n"
                    f"Air Quality: {d['air_qual_raw']}")

            if text_id != -1: p.removeUserDebugItem(text_id)
            # Center of the room
            text_id = p.addUserDebugText(info, [-2, 0, 3], textColorRGB=[1, 1, 1], textSize=2.0)
        else:
            if text_id != -1: p.removeUserDebugItem(text_id)
            text_id = p.addUserDebugText("Waiting for Sensor Stream...", [-2, 0, 2], textColorRGB=[1,1,1], textSize=1.5)

        p.stepSimulation()
        time.sleep(0.1)

# ==========================================
# --- MAIN PROCESS ---
# ==========================================
def main_server():
    conn = sqlite3.connect(DB_FILE)
    conn.execute('CREATE TABLE IF NOT EXISTS hvac_logs (timestamp DATETIME, status TEXT, mA REAL, real_t REAL, exp_t REAL, health REAL)')
    conn.commit()
    conn.close()
    engine = DigitalTwinEngine()

    def on_message(client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
            t = engine.analyze(payload)
            db = sqlite3.connect(DB_FILE)
            db.execute("INSERT INTO hvac_logs VALUES (?,?,?,?,?,?)", (datetime.now(), payload["health"]["sys_status"], payload["data"]["motor_mA"], payload["data"]["motor_temp_C"], t["exp_t"], t["health"]))
            db.commit(); db.close()
            os.system('cls' if os.name == 'nt' else 'clear')
            print(f"--- FOG NODE ACTIVE ---\nDATA RECEIVED: {datetime.now().strftime('%H:%M:%S')}\nSTATUS: {payload['health']['sys_status']}\n-----------------------")
        except: pass

    client = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION2)
    client.on_message = on_message
    client.connect(MQTT_BROKER_HOST, 1883)
    client.subscribe(MQTT_TOPIC)
    client.loop_forever()

if __name__ == "__main__":
    multiprocessing.freeze_support()
    p1 = multiprocessing.Process(target=run_hvac_window)
    p2 = multiprocessing.Process(target=run_room_window)
    p1.start()
    p2.start()
    try:
        main_server()
    except KeyboardInterrupt:
        p1.terminate()
        p2.terminate()