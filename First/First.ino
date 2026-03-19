#include <WiFi.h>
#include <PubSubClient.h>
#include <Wire.h>
#include <Adafruit_INA219.h>
#include <Adafruit_BME280.h>
#include <ArduinoJson.h>

// ==========================================
// --- CONFIGURATION ---
// ==========================================
const char* ssid = "Navya";
const char* password = "8870527878";
const char* mqtt_server = "192.168.29.97";
const char* mqtt_topic = "telemetry/room1/sensors";

// --- PINS ---
#define PIN_SDA 21
#define PIN_SCL 22
#define PIN_MQ135 34
#define PIN_LED_GREEN 4   // Network Status
#define PIN_LED_RED 15    // Sensor Fault
#define PIN_LED_TRIP 13   // Critical Overcurrent

// --- SAFETY THRESHOLDS ---
#define TRIP_CURRENT_mA 3000.0 // 3 Amps - Critical threshold
#define NUM_INA_SAMPLES 20     // Smoothing for current sensor

// ==========================================
// --- SHARED DATA STRUCTURE & RTOS ---
// ==========================================
struct SensorData {
  float current_mA;
  float temp_c;
  float pressure_hPa;
  float humidity_rh;
  int   mq135_raw;
  float mq135_volts;
  bool  ina_ok;
  bool  bme_ok;
  bool  mq_ok;
  bool  is_tripped;
  bool  updated;
};

SensorData sharedData;
SemaphoreHandle_t dataMutex;

Adafruit_INA219 ina219;
Adafruit_BME280 bme;
WiFiClient espClient;
PubSubClient client(espClient);

TaskHandle_t TaskSensors;
TaskHandle_t TaskComms;

// --- MATH HELPER ---
float round2Decimals(float value) {
  return round(value * 100.0) / 100.0;
}

// ==========================================
// --- SETUP ---
// ==========================================
void setup() {
  Serial.begin(115200);
  delay(500);
  
  // 1. Setup Pins & LEDs
  pinMode(PIN_MQ135, INPUT);
  pinMode(PIN_LED_GREEN, OUTPUT);
  pinMode(PIN_LED_RED, OUTPUT);
  pinMode(PIN_LED_TRIP, OUTPUT);
  
  sharedData.is_tripped = false;

  // Visual Boot Sequence (All LEDs ON)
  digitalWrite(PIN_LED_GREEN, HIGH);
  digitalWrite(PIN_LED_RED, HIGH);
  digitalWrite(PIN_LED_TRIP, HIGH);

  // 2. Init RTOS Mutex
  dataMutex = xSemaphoreCreateMutex();

  // 3. Init I2C
  Wire.begin(PIN_SDA, PIN_SCL);
  delay(500);

  // 4. Built-In Self-Test (BIST)
  Serial.println("\n--- SYSTEM BOOT: HEALTH CHECKS ---");
  
  if (!ina219.begin()) {
    Serial.println("FAULT: INA219 not found!");
    sharedData.ina_ok = false;
  } else {
    Serial.println("OK: INA219 Ready.");
    sharedData.ina_ok = true;
  }

  if (!bme.begin(0x76) && !bme.begin(0x77)) {
    Serial.println("FAULT: BME280 not found!");
    sharedData.bme_ok = false;
  } else {
    Serial.println("OK: BME280 Ready.");
    sharedData.bme_ok = true;
  }
  
  sharedData.mq_ok = true; // Checked dynamically in loop

  // Turn LEDs OFF after boot test
  digitalWrite(PIN_LED_GREEN, LOW);
  digitalWrite(PIN_LED_RED, LOW);
  digitalWrite(PIN_LED_TRIP, LOW);

  // 5. Start Dual-Core Tasks
  Serial.println("Starting Core 1 (Sensors)...");
  xTaskCreatePinnedToCore(taskSensorsCode, "Sensors", 10000, NULL, 1, &TaskSensors, 1);
  delay(500);
  Serial.println("Starting Core 0 (Comms)...");
  xTaskCreatePinnedToCore(taskCommsCode, "Comms", 16000, NULL, 1, &TaskComms, 0);
}

void loop() {
  vTaskDelete(NULL); // Delete standard Arduino loop to save memory
}

// ==========================================
// --- CORE 1: SENSING & FAULT DETECTION ---
// ==========================================
void taskSensorsCode(void * parameter) {
  for(;;) {
    // If system tripped, freeze readings to preserve crash data
    if (sharedData.is_tripped) {
      vTaskDelay(1000 / portTICK_PERIOD_MS);
      continue;
    }

    float temp_cur = 0, t_c = 0, p_hpa = 0, h_rh = 0;
    int mq_raw = 0;
    
    // 1. Read & Average INA219
    if (sharedData.ina_ok) {
      float total_mA = 0;
      for (int i = 0; i < NUM_INA_SAMPLES; i++) {
        total_mA += ina219.getCurrent_mA();
        delay(1);
      }
      temp_cur = round2Decimals(total_mA / NUM_INA_SAMPLES);
    }

    // 2. Read BME280
    if (sharedData.bme_ok) {
      t_c = round2Decimals(bme.readTemperature());
      p_hpa = round2Decimals(bme.readPressure() / 100.0F);
      h_rh = round2Decimals(bme.readHumidity());
    }

    // 3. Read MQ-135
    long mq_sum = 0;
    for(int i = 0; i < 10; i++) { 
      mq_sum += analogRead(PIN_MQ135); 
      delay(2); 
    }
    mq_raw = mq_sum / 10;
    float mq_volts = round2Decimals((mq_raw / 4095.0) * 3.3 * 2.0); // 2:1 Divider logic
    bool local_mq_ok = (mq_raw > 20 && mq_raw < 3200);
    
    // 4. Check for Critical Trip
    bool tripped_now = (temp_cur > TRIP_CURRENT_mA);
    
    // 5. Lock & Update Shared Memory
    if (xSemaphoreTake(dataMutex, portMAX_DELAY) == pdTRUE) {
      sharedData.current_mA = temp_cur;
      sharedData.temp_c = t_c;
      sharedData.pressure_hPa = p_hpa;
      sharedData.humidity_rh = h_rh;
      sharedData.mq135_raw = mq_raw;
      sharedData.mq135_volts = mq_volts;
      sharedData.mq_ok = local_mq_ok;
      
      if (tripped_now) sharedData.is_tripped = true; // Latching logic
      
      sharedData.updated = true;
      xSemaphoreGive(dataMutex);
    }

    vTaskDelay(100 / portTICK_PERIOD_MS); // Run at ~10Hz
  }
}

// ==========================================
// --- CORE 0: WIFI, MQTT & LED LOGIC ---
// ==========================================
void taskCommsCode(void * parameter) {
  Serial.println("Core 0 Initialized.");
  
  WiFi.mode(WIFI_STA);
  client.setServer(mqtt_server, 1883);
  
  unsigned long lastPublish = 0;
  bool is_connecting_wifi = false;

  for(;;) {
    // --- 1. READ STATUS FROM SENSORS ---
    bool has_fault = false;
    bool is_tripped = false;
    if (xSemaphoreTake(dataMutex, 10) == pdTRUE) {
      has_fault = (!sharedData.ina_ok || !sharedData.bme_ok || !sharedData.mq_ok);
      is_tripped = sharedData.is_tripped;
      xSemaphoreGive(dataMutex);
    }

    // --- 2. HARDWARE LED STATUS ---
    digitalWrite(PIN_LED_TRIP, is_tripped ? HIGH : LOW);
    digitalWrite(PIN_LED_RED, (has_fault && !is_tripped) ? HIGH : LOW);

    // --- 3. ROBUST WIFI & MQTT LOGIC ---
    if (WiFi.status() != WL_CONNECTED) {
      digitalWrite(PIN_LED_GREEN, millis() % 1000 < 500); // Slow blink
      
      if (!is_connecting_wifi) {
        Serial.println("WiFi Disconnected. Attempting connection...");
        WiFi.disconnect();
        delay(100);
        WiFi.begin(ssid, password);
        is_connecting_wifi = true;
      }
    } 
    else {
      is_connecting_wifi = false; // Reset flag once connected
      
      if (!client.connected()) {
        digitalWrite(PIN_LED_GREEN, millis() % 200 < 100); // Fast blink
        Serial.print("Connecting to MQTT broker at ");
        Serial.print(mqtt_server);
        
        // Connect to MQTT Broker
        if (client.connect("CentralSensorNode_01")) { 
          Serial.println(" ... CONNECTED!");
        } else {
          Serial.print(" ... FAILED, rc=");
          Serial.println(client.state());
          vTaskDelay(3000 / portTICK_PERIOD_MS); // Wait before retrying MQTT
        }
      } 
      else {
        // --- ALL CONNECTED: PUBLISH DATA ---
        digitalWrite(PIN_LED_GREEN, HIGH); // Solid Green
        client.loop(); // Keep MQTT alive
        
        if (millis() - lastPublish > 1000) {
          lastPublish = millis();
          
          if (xSemaphoreTake(dataMutex, 100) == pdTRUE) {
            if (sharedData.updated) {
              
              // Build JSON Payload
              StaticJsonDocument<512> doc;
              doc["node_id"] = "hvac_monitor_01";
              doc["uptime_s"] = millis() / 1000;

              // Health Status Object
              JsonObject health = doc.createNestedObject("health");
              health["ina219"] = sharedData.ina_ok;
              health["bme280"] = sharedData.bme_ok;
              health["mq135"]  = sharedData.mq_ok;
              health["trip_status"] = sharedData.is_tripped;
              
              if (sharedData.is_tripped) health["sys_status"] = "TRIPPED";
              else if (has_fault)        health["sys_status"] = "DEGRADED";
              else                       health["sys_status"] = "OPTIMAL";

              // Sensor Data Object
              JsonObject data = doc.createNestedObject("data");
              data["motor_mA"]   = sharedData.current_mA;
              data["env_temp_C"] = sharedData.temp_c;
              data["env_hum_RH"] = sharedData.humidity_rh;
              data["env_pres_hPa"]= sharedData.pressure_hPa;
              data["air_qual_raw"]= sharedData.mq135_raw;
              data["air_qual_V"]  = sharedData.mq135_volts;

              sharedData.updated = false;
              xSemaphoreGive(dataMutex);

              // Transmit to Linux Machine
              char buffer[512];
              serializeJson(doc, buffer);
              client.publish(mqtt_topic, buffer);
              Serial.print("Published: ");
              Serial.println(buffer);
            } else {
              xSemaphoreGive(dataMutex);
            }
          }
        }
      }
    }
    
    // Yield to FreeRTOS (CRITICAL for stability)
    vTaskDelay(50 / portTICK_PERIOD_MS); 
  }
}
