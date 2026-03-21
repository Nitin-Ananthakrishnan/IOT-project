import pybullet as p
import pybullet_data
import time
import random

p.connect(p.GUI)
p.configureDebugVisualizer(p.COV_ENABLE_GUI, 0)
p.setAdditionalSearchPath(pybullet_data.getDataPath())
p.setGravity(0, 0, 0)

# Helper to create objects
def create_box(pos, size, color):
    col = p.createCollisionShape(p.GEOM_BOX, halfExtents=size)
    vis = p.createVisualShape(p.GEOM_BOX, halfExtents=size, rgbaColor=color)
    return p.createMultiBody(0, col, vis, basePosition=pos)

# 1. Create a 5-walled structure
def create_5_walled_layer(z_pos):
    create_box([0, 1.0, z_pos], [2.5, 0.1, 0.5], [0.5, 0.5, 0.5, 1])
    create_box([-2.5, 0, z_pos], [0.1, 1.0, 0.5], [0.5, 0.5, 0.5, 1])
    create_box([2.5, 0, z_pos], [0.1, 1.0, 0.5], [0.5, 0.5, 0.5, 1])
    create_box([0, 0, z_pos + 0.5], [2.6, 1.1, 0.1], [0.4, 0.4, 0.4, 1]) 
    create_box([0, 0, z_pos - 0.5], [2.6, 1.1, 0.1], [0.4, 0.4, 0.4, 1])

create_5_walled_layer(2.5) # Top Layer
create_5_walled_layer(0.5) # Bottom Layer

# 2. Green Motors
motor_top = create_box([-1.5, 0, 2.6], [0.5, 0.6, 0.5], [0, 0.8, 0, 0.7])
motor_bot = create_box([-1.5, 0, 0.5], [0.5, 0.6, 0.5], [0, 0.8, 0, 0.7])

# New: State tracking for Motors
m_states = {"top": [False, 24.0, -1, -1], "bot": [False, 22.0, -1, -1]}

def refresh_motor_labels():
    global m_states
    for side in ["top", "bot"]:
        m = m_states[side]
        z = 3.4 if side == "top" else 1.4
        
        # Remove old text
        if m[2] != -1: p.removeUserDebugItem(m[2])
        if m[3] != -1: p.removeUserDebugItem(m[3])
        
        # 1. Status Part (Green/Red)
        status = "ON" if m[0] else "OFF"
        col = [0, 1, 0] if m[0] else [1, 0, 0]
        m[2] = p.addUserDebugText(f"MOTOR {status}", [-2.0, 0, z], textColorRGB=col, textSize=1.2)
        
        # 2. Temperature Part (Purple)
        # We offset the x-coordinate slightly to the right to simulate being on the same line
        m[3] = p.addUserDebugText(f"           |Temp: {m[1]} C", [-1.5, 0, z], textColorRGB=[0.5, 0, 0.5], textSize=1.2)
refresh_motor_labels()

# 3. Central Door
door_id = None
text_id = -1

def update_door(is_open):
    global text_id, door_id
    # Remove existing door and text objects
    if door_id: p.removeBody(door_id)
    if text_id != -1: p.removeUserDebugItem(text_id)
    
    # Logic: Green for OPEN, Red for CLOSED
    if is_open:
        color = [0, 0.6, 0, 1]  # Green color for door
        msg = "OPEN"
        text_color = [0, 1, 0]  # Green text
    else:
        color = [0.6, 0, 0, 1]  # Red color for door
        msg = "CLOSED"
        text_color = [1, 0, 0]  # Red text
    
    # Create the door box
    door_id = create_box([0.5, 0, 1.5], [0.15, 0.1, 0.5], color)
    
    # Create the label with the matching color
    text_id = p.addUserDebugText(msg, [0.7, 0, 1.5], textColorRGB=text_color, textSize=1.5)
p.resetDebugVisualizerCamera(4, 0, 0.5, [0, 0, 2.25]) # Scaled to 70%
update_door(False)

while True:
    keys = p.getKeyboardEvents()
    if ord('o') in keys and keys[ord('o')] & p.KEY_WAS_TRIGGERED: update_door(True)
    if ord('c') in keys and keys[ord('c')] & p.KEY_WAS_TRIGGERED: update_door(False)
    
    # Motor controls
    if ord('1') in keys and keys[ord('1')] & p.KEY_WAS_TRIGGERED:
        m_states["top"][0] = not m_states["top"][0]
        m_states["top"][1] = round(random.uniform(20, 30), 1)
        refresh_motor_labels()
    if ord('2') in keys and keys[ord('2')] & p.KEY_WAS_TRIGGERED:
        m_states["bot"][0] = not m_states["bot"][0]
        m_states["bot"][1] = round(random.uniform(20, 30), 1)
        refresh_motor_labels()
        
    p.stepSimulation()
    time.sleep(1./240.)