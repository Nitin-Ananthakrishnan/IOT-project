import pybullet as p
import pybullet_data
import time
import random

# Initialize
p.connect(p.GUI)
p.configureDebugVisualizer(p.COV_ENABLE_GUI, 0)
p.setAdditionalSearchPath(pybullet_data.getDataPath())
p.setGravity(0, 0, 0)

# Build the room
def create_colored_box(pos, size, color):
    col_shape = p.createCollisionShape(p.GEOM_BOX, halfExtents=size)
    visual_shape = p.createVisualShape(p.GEOM_BOX, halfExtents=size, rgbaColor=color)
    p.createMultiBody(0, col_shape, visual_shape, basePosition=pos)

create_colored_box([0, 0, -0.1], [5, 5, 0.1], [0.2, 0.2, 0.2, 1])
wall_color = [0.2, 0.4, 0.6, 1]
create_colored_box([0, 5, 1], [5, 0.1, 1], wall_color)
create_colored_box([0, -5, 1], [5, 0.1, 1], wall_color)
create_colored_box([5, 0, 1], [0.1, 5, 1], wall_color)
create_colored_box([-5, 0, 1], [0.1, 5, 1], wall_color)

p.resetDebugVisualizerCamera(cameraDistance=8, cameraYaw=45, cameraPitch=-20, cameraTargetPosition=[0,0,1])

text_id = -1

while True:
    # Sensor Data
    temp = round(random.uniform(20.0, 24.0), 1)
    pressure = round(random.uniform(1010, 1015), 1)
    humidity = round(random.uniform(30, 50), 1)
    air_quality = random.choice(["Good", "Fair", "Poor"])

    info = (f"HVAC SYSTEM\n"
            f"Temp: {temp} C\n"
            f"Press: {pressure} hPa\n"
            f"Humid: {humidity} %\n"
            f"Air Q: {air_quality}")

    if text_id != -1:
        p.removeUserDebugItem(text_id)

    # KEY CHANGE: Using a standard UI position. 
    # Try different values here if your screen resolution is very large.
    # [0.5, 0.5, 0] is usually top-right corner area in PyBullet's coordinate mapping
    text_id = p.addUserDebugText(
        info, 
        [0.5, 0.4, 0], 
        textColorRGB=[1, 1, 1], 
        textSize=1.5,
        parentObjectUniqueId=-1
    )

    p.stepSimulation()
    time.sleep(0.5)