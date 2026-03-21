import pybullet as p
import pybullet_data
import time

p.connect(p.GUI)
p.configureDebugVisualizer(p.COV_ENABLE_GUI, 0)
p.setAdditionalSearchPath(pybullet_data.getDataPath())
p.setGravity(0, 0, 0)

# Helper to create objects
def create_box(pos, size, color):
    col = p.createCollisionShape(p.GEOM_BOX, halfExtents=size)
    vis = p.createVisualShape(p.GEOM_BOX, halfExtents=size, rgbaColor=color)
    return p.createMultiBody(0, col, vis, basePosition=pos)

# 1. Structure
def create_5_walled_layer(z_pos):
    create_box([0, 1.0, z_pos], [2.5, 0.1, 0.5], [0.5, 0.5, 0.5, 1])
    create_box([-2.5, 0, z_pos], [0.1, 1.0, 0.5], [0.5, 0.5, 0.5, 1])
    create_box([2.5, 0, z_pos], [0.1, 1.0, 0.5], [0.5, 0.5, 0.5, 1])
    create_box([0, 0, z_pos + 0.5], [2.6, 1.1, 0.1], [0.4, 0.4, 0.4, 1]) 
    create_box([0, 0, z_pos - 0.5], [2.6, 1.1, 0.1], [0.4, 0.4, 0.4, 1])

create_5_walled_layer(2.5) 
create_5_walled_layer(0.5) 

# Motor states
motor_states = {"top": False, "bot": False}
motor_text_ids = {"top": -1, "bot": -1}

def update_motor_labels():
    global motor_text_ids
    # Remove old text
    if motor_text_ids["top"] != -1: p.removeUserDebugItem(motor_text_ids["top"])
    if motor_text_ids["bot"] != -1: p.removeUserDebugItem(motor_text_ids["bot"])
    
    # Draw updated text
    t_msg = "ON" if motor_states["top"] else "OFF"
    t_col = [0, 1, 0] if motor_states["top"] else [1, 0, 0]
    motor_text_ids["top"] = p.addUserDebugText(f"MOTOR {t_msg}", [-1.5, 0, 3.4], textColorRGB=t_col, textSize=1.2)
    
    b_msg = "ON" if motor_states["bot"] else "OFF"
    b_col = [0, 1, 0] if motor_states["bot"] else [1, 0, 0]
    motor_text_ids["bot"] = p.addUserDebugText(f"MOTOR {b_msg}", [-1.5, 0, 1.4], textColorRGB=b_col, textSize=1.2)

# Initial draw
motor_top = create_box([-1.5, 0, 2.6], [0.5, 0.6, 0.5], [0, 0.8, 0, 0.7])
motor_bot = create_box([-1.5, 0, 0.5], [0.5, 0.6, 0.5], [0, 0.8, 0, 0.7])
update_motor_labels()

# 2. Central Door
door_id, door_text_id = None, -1
def update_door(is_open):
    global door_text_id, door_id
    if door_id: p.removeBody(door_id)
    if door_text_id != -1: p.removeUserDebugItem(door_text_id)
    color = [0.6, 0, 0, 1] if not is_open else [0, 0.6, 0, 1]
    msg = "CLOSED" if not is_open else "OPEN"
    door_id = create_box([0.5, 0, 1.5], [0.15, 0.1, 0.5], color)
    door_text_id = p.addUserDebugText(msg, [0.7, 0, 1.5], textColorRGB=[1, 1, 1], textSize=1.5)

p.resetDebugVisualizerCamera(10, 0, 0, [0, 0, 1.5])
update_door(False)

print("Controls: 'o'/'c' Door, '1' Top Motor, '2' Bottom Motor")

while True:
    keys = p.getKeyboardEvents()
    if ord('o') in keys and keys[ord('o')] & p.KEY_WAS_TRIGGERED: update_door(True)
    if ord('c') in keys and keys[ord('c')] & p.KEY_WAS_TRIGGERED: update_door(False)
    if ord('1') in keys and keys[ord('1')] & p.KEY_WAS_TRIGGERED:
        motor_states["top"] = not motor_states["top"]
        update_motor_labels()
    if ord('2') in keys and keys[ord('2')] & p.KEY_WAS_TRIGGERED:
        motor_states["bot"] = not motor_states["bot"]
        update_motor_labels()
        
    p.stepSimulation()
    time.sleep(1./240.)