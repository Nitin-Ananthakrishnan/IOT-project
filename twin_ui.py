import multiprocessing
import pybullet as p
import pybullet_data
import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion
import json
import time
import sys

# --- CONFIG ---
# If running this on your Main Laptop, and the Mosquitto broker is on the Old Laptop, 
# change "localhost" to the Old Laptop's IP address (e.g., "192.168.1.100").
MQTT_BROKER = "10.93.21.157" 
MQTT_TOPIC = "telemetry/room1/sensors"

# ==========================================
# --- WINDOW 1: HVAC MECHANICAL TWIN ---
# ==========================================
def run_hvac_window():
    try:
        p.connect(p.GUI)
        time.sleep(1) 
        p.configureDebugVisualizer(p.COV_ENABLE_GUI, 0)
        p.setAdditionalSearchPath(pybullet_data.getDataPath())
        p.setGravity(0, 0, 0)
        
        def create_box(pos, size, color):
            vis = p.createVisualShape(p.GEOM_BOX, halfExtents=size, rgbaColor=color)
            return p.createMultiBody(0, -1, vis, basePosition=pos)

        # Build Frame
        for z in [0.5, 2.5]:
            create_box([0, 1.0, z], [2.5, 0.1, 0.5], [0.5, 0.5, 0.5, 1])
            create_box([-2.5, 0, z], [0.1, 1.0, 0.5], [0.5, 0.5, 0.5, 1])
            create_box([2.5, 0, z], [0.1, 1.0, 0.5], [0.5, 0.5, 0.5, 1])
            create_box([0, 0, z + 0.5], [2.6, 1.1, 0.1], [0.4, 0.4, 0.4, 1]) 
            create_box([0, 0, z - 0.5], [2.6, 1.1, 0.1], [0.4, 0.4, 0.4, 1])
        
        create_box([-1.5, 0, 2.6], [0.5, 0.6, 0.5], [0, 0.8, 0, 0.7]) 
        create_box([-1.5, 0, 0.5], [0.5, 0.6, 0.5], [0, 0.8, 0, 0.7]) 

        # Permanent Door
        vis_door = p.createVisualShape(p.GEOM_BOX, halfExtents=[0.15, 0.1, 0.5], rgbaColor=[0, 0.6, 0, 1])
        door_id = p.createMultiBody(0, -1, vis_door, basePosition=[0.5, 0, 1.5])

        text_items = []
        local_data = None

        def on_msg(c, u, m):
            nonlocal local_data
            try: local_data = json.loads(m.payload.decode())
            except: pass

        cli = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION2)
        cli.username_pw_set("hvac_admin", "iot_secure_123")
        cli.on_message = on_msg
        cli.connect(MQTT_BROKER, 1883)
        cli.subscribe(MQTT_TOPIC)
        cli.loop_start()
        
        p.resetDebugVisualizerCamera(4, 0, 0.5, [0, 0, 2.25])

        while True:
            for item in text_items: 
                try: p.removeUserDebugItem(item)
                except: pass
            text_items.clear()

            if local_data:
                d = local_data["data"]; h = local_data["health"]
                is_fault = (h.get("software_trip", False) or h.get("trip_status", False))
                
                # 1. HVAC SYSTEM BILLBOARD (NEW)
                info = (f"HVAC SYSTEM\n"
                        f"Temp: {d['motor_temp_C']} C\n"
                        f"Load: {d['motor_mA']} mA\n"
                        f"Air Q: {d['air_qual_raw']}")
                text_items.append(p.addUserDebugText(info, [1.5, 0, 0], [1, 1, 1], 1.2))

                # 2. Status Labels
                status_txt = "FAULT" if is_fault else ("ON" if d["motor_mA"] > 5 else "OFF")
                text_col = [1, 0, 0] if is_fault else ([0, 1, 0] if d["motor_mA"] > 5 else [1, 1, 1])
                text_items.append(p.addUserDebugText(f"MOTOR {status_txt}", [-2.0, 0, 3.4], text_col, 1.2))

                # 3. Door Visualizer
                door_col = [0.8, 0, 0, 1] if is_fault else [0, 0.6, 0, 1]
                p.changeVisualShape(door_id, -1, rgbaColor=door_col)
                text_items.append(p.addUserDebugText("CLOSED" if is_fault else "OPEN", [0.7, 0, 1.5], door_col[:3], 1.5))
            
            p.stepSimulation()
            time.sleep(0.1)
    except Exception as e:
        print(f"HVAC Window Error: {e}")
# ==========================================
# --- WINDOW 2: ROOM BALLROOM ---
# ==========================================
def run_room_window():
    try:
        # THE FIX: Wait 2 seconds before trying to open the second 3D window
        # This stops the graphics driver from crashing on Windows.
        time.sleep(2) 
        
        p.connect(p.GUI)
        p.configureDebugVisualizer(p.COV_ENABLE_GUI, 0)
        p.setAdditionalSearchPath(pybullet_data.getDataPath())
        p.setGravity(0, 0, 0)

        def create_colored_box(pos, size, color):
            vis = p.createVisualShape(p.GEOM_BOX, halfExtents=size, rgbaColor=color)
            p.createMultiBody(0, -1, vis, basePosition=pos)

        # Build Room
        create_colored_box([0, 0, -0.1], [5, 5, 0.1], [0.2, 0.2, 0.2, 1])
        wall_color = [0.2, 0.4, 0.6, 1]
        create_colored_box([0, 5, 1], [5, 0.1, 1], wall_color)
        create_colored_box([0, -5, 1], [5, 0.1, 1], wall_color)
        create_colored_box([5, 0, 1], [0.1, 5, 1], wall_color)
        create_colored_box([-5, 0, 1], [0.1, 5, 1], wall_color)

        p.resetDebugVisualizerCamera(8, 45, -20, [0,0,1])

        text_id = -1
        local_data = None

        def on_msg(c, u, m):
            nonlocal local_data
            try: local_data = json.loads(m.payload.decode())
            except: pass

        cli = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION2)
        cli.username_pw_set("hvac_admin", "iot_secure_123")
        cli.on_message = on_msg
        cli.connect(MQTT_BROKER, 1883)
        cli.subscribe(MQTT_TOPIC)
        cli.loop_start()

        while True:
            if local_data:
                d = local_data["data"]
                info = (f"HVAC SYSTEM\n"
                        f"Temp: {d['env_temp_C']} C\n"
                        f"Press: {d['env_pres_hPa']} hPa\n"
                        f"Humid: {d['env_hum_RH']} %\n"
                        f"Air Q: {d['air_qual_raw']}")

                if text_id != -1: 
                    try: p.removeUserDebugItem(text_id)
                    except: pass
                text_id = p.addUserDebugText(info, [0.5, 0.4, 0], [1, 1, 1], 1.5)

            p.stepSimulation()
            time.sleep(0.5)
            
    except Exception as e:
        print(f"Room Window Error: {e}")

if __name__ == "__main__":
    multiprocessing.freeze_support()
    p1 = multiprocessing.Process(target=run_hvac_window)
    p2 = multiprocessing.Process(target=run_room_window)
    
    print("Starting 3D Digital Twin Visualizer...")
    p1.start()
    p2.start()
    
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt:
        print("Closing Visualizer...")
        p1.terminate()
        p2.terminate()