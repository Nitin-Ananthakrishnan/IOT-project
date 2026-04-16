#include <ESP8266WiFi.h>
#include <PubSubClient.h>
#include <Servo.h>

// --- CONFIG ---
#include "paswd.h"

// --- PINS ---
#define MOTOR_PWM D1
#define MOTOR_DIR D2
#define SERVO1_PIN D5 
#define SERVO2_PIN D6 

Servo servo1;
Servo servo2;
WiFiClient espClient;
PubSubClient client(espClient);
bool systemHalted = false;

void callback(char* topic, byte* payload, unsigned int length) {
  String msg = "";
  for (int i = 0; i < length; i++) { msg += (char)payload[i]; }

  if (msg == "STOP") {
    systemHalted = true;
    analogWrite(MOTOR_PWM, 0);
    return;
  }
  if (msg == "RESUME") { systemHalted = false; return; }

  if (!systemHalted) {
    // Expected format: "speed,s1,s2"
    int firstComma = msg.indexOf(',');
    int lastComma = msg.lastIndexOf(',');
    
    if (firstComma > 0 && lastComma > firstComma) {
      int speed = msg.substring(0, firstComma).toInt();
      int s1 = msg.substring(firstComma + 1, lastComma).toInt();
      int s2 = msg.substring(lastComma + 1).toInt();

      // 1. Update Motor
      analogWrite(MOTOR_PWM, speed);
      digitalWrite(MOTOR_DIR, LOW);

      // 2. Update Servos with "Attach-Move-Detach" Jitter Fix
      servo1.attach(SERVO1_PIN, 500, 2400);
      servo2.attach(SERVO2_PIN, 500, 2400);
      
      servo1.write(s1);
      servo2.write(s2);

      // Wait for physical travel time
      delay(1000); 
      
      // KILL POWER to the signal (Stops vibration/humming)
      servo1.detach();
      servo2.detach();
      
      Serial.println("Actuators Adjusted and Signal Locked.");
    }
  }
}

void setup_wifi() {
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) { delay(500); Serial.print("."); }
  Serial.println("\nWiFi Connected");
}

void reconnect() {
  while (!client.connected()) {
    if (client.connect("HVAC_Actuator_EndNode", "hvac_admin", "iot_secure_123")) {
      client.subscribe("command/room1/actuators");
    } else { delay(5000); }
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(MOTOR_PWM, OUTPUT);
  pinMode(MOTOR_DIR, OUTPUT);
  analogWriteRange(100);
  analogWriteFreq(1000);
  
  // Initial Safety State
  servo1.write(0);
  servo2.write(180);

  setup_wifi();
  client.setServer(mqtt_server, 1883);
  client.setCallback(callback);
}

void loop() {
  if (!client.connected()) reconnect();
  client.loop();
}
