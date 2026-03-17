#include <WiFi.h>
#include <PubSubClient.h>
#include <Wire.h>
#include <Adafruit_INA219.h>
#include <Adafruit_BME280.h>
#include <ArduinoJson.h>

// --- CONFIGURATION ---
const char* ssid = "Nitin";
const char* password = "22345677";
const char* mqtt_server = "10.178.88.157"; // Your Edge Node / Pi IP
const char* mqtt_topic = "telemetry/room1/sensors";

// --- PINS ---
#define PIN_SDA 21
#define PIN_SCL 22
#define PIN_MQ135 34
#define PIN_LED_GREEN 4
#define PIN_LED_RED 15

// --- AVERAGING CONFIG ---
#define NUM_INA_SAMPLES 20

// --- SHARED DATA STRUCTURE (Cross-Core Memory) ---
struct SensorData {
  float current_mA;
  float bus_voltage_V;
  float temp_c;
  float pressure_hPa;
  float humidity_rh;
  int   mq135_raw;
  float mq135_volts;
  bool  ina_ok;
  bool  bme_ok;
  bool  mq_ok;
  bool  updated;
};

SensorData sharedData;
SemaphoreHandle_t dataMutex;

// --- OBJECTS ---
Adafruit_INA219 ina219;
Adafruit_BME280 bme;
WiFiClient espClient;
PubSubClient client(espClient);

// Task Handles
TaskHandle_t TaskSensors;
TaskHandle_t TaskComms;

// --- MATH HELPER: Round to 2 Decimals ---
float round2Decimals(float value) {
  return round(value * 100.0) / 100.0;
}

// --- SETUP ---
void setup() {
  Serial.begin(115200);
  
  // 1. Setup Pins
  pinMode(PIN_MQ135, INPUT);
  pinMode(PIN_LED_GREEN, OUTPUT);
  pinMode(PIN_LED_RED, OUTPUT);
  
  // Turn both LEDs on for boot sequence
  digitalWrite(PIN_LED_GREEN, HIGH);
  digitalWrite(PIN_LED_RED, HIGH);

  // 2. Create Mutex for Thread Safety
  dataMutex = xSemaphoreCreateMutex();

  // 3. Initialize I2C
  Wire.begin(PIN_SDA, PIN_SCL);
  delay(500);

  // 4. Run Initial Health Checks (BIST)
  Serial.println("--- SYSTEM BOOT: HEALTH CHECKS ---");
  
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
  
  sharedData.mq_ok = true; // Will dynamically validate in loop

  // Turn LEDs off after boot
  digitalWrite(PIN_LED_GREEN, LOW);
  digitalWrite(PIN_LED_RED, LOW);

  // 5. Start Dual-Core Tasks
  Serial.println("Starting core 1");
  xTaskCreatePinnedToCore(taskSensorsCode, "Sensors", 10000, NULL, 1, &TaskSensors, 1); // Core 1
  delay(500);
  Serial.println("Starting core 0");
  xTaskCreatePinnedToCore(taskCommsCode, "Comms", 10000, NULL, 1, &TaskComms, 0);     // Core 0
}

void loop() {
  vTaskDelete(NULL); // Delete standard loop to save RAM
}

// ==========================================
// CORE 1: HIGH-SPEED SENSING & AVERAGING
// ==========================================
void taskSensorsCode(void * parameter) {
  for(;;) {
    float temp_cur = 0, temp_volt = 0, t_c = 0, p_hpa = 0, h_rh = 0;
    int mq_raw = 0;
    
    // --- 1. INA219 (Averaging & Rounding) ---
    if (sharedData.ina_ok) {
      float total_mA = 0;
      float total_V = 0;
      for (int i = 0; i < NUM_INA_SAMPLES; i++) {
        total_mA += ina219.getCurrent_mA();
        total_V += ina219.getBusVoltage_V() + (ina219.getShuntVoltage_mV() / 1000.0);
        delay(1);
      }
      temp_cur = round2Decimals(total_mA / NUM_INA_SAMPLES);
      temp_volt = round2Decimals(total_V / NUM_INA_SAMPLES);
    }

    // --- 2. BME280 ---
    if (sharedData.bme_ok) {
      t_c = round2Decimals(bme.readTemperature());
      p_hpa = round2Decimals(bme.readPressure() / 100.0F);
      h_rh = round2Decimals(bme.readHumidity());
    }

    // --- 3. MQ-135 (Oversampling + Math) ---
    long mq_sum = 0;
    for(int i = 0; i < 10; i++) {
      mq_sum += analogRead(PIN_MQ135);
      delay(2);
    }
    mq_raw = mq_sum / 10;
    // Calculate real voltage factoring in the 2:1 Voltage Divider
    float mq_volts = round2Decimals((mq_raw / 4095.0) * 3.3 * 2.0);

    // --- 4. DATA SANITY CHECKS ---
    bool local_mq_ok = (mq_raw > 20 && mq_raw < 3200); // Fails if wire cut (0) or shorted to 5V (>3200)
    
    // --- 5. LOCK & UPDATE SHARED DATA ---
    if (xSemaphoreTake(dataMutex, portMAX_DELAY) == pdTRUE) {
      sharedData.current_mA = temp_cur;
      sharedData.bus_voltage_V = temp_volt;
      sharedData.temp_c = t_c;
      sharedData.pressure_hPa = p_hpa;
      sharedData.humidity_rh = h_rh;
      sharedData.mq135_raw = mq_raw;
      sharedData.mq135_volts = mq_volts;
      sharedData.mq_ok = local_mq_ok;
      sharedData.updated = true;
      
      xSemaphoreGive(dataMutex);
    }

    vTaskDelay(100 / portTICK_PERIOD_MS); // Run sensor cycle every ~100ms
  }
}

// ==========================================
// CORE 0: WIFI, MQTT & LED STATUS
// ==========================================
// ==========================================
// CORE 0: WIFI, MQTT & LED STATUS
// ==========================================
void taskCommsCode(void * parameter) {
  Serial.println("\n--- Core 0: taskCommsCode started! ---");

  // 1. Force Wi-Fi into Station Mode and clear old connections
  WiFi.mode(WIFI_STA);
  WiFi.disconnect(true);
  vTaskDelay(1000 / portTICK_PERIOD_MS);

  // 2. Connect to Wi-Fi
  Serial.print("Connecting to WiFi Network: ");
  Serial.println(ssid);
  WiFi.begin(ssid, password);
  
  // Print dots while waiting for Wi-Fi to connect
  while (WiFi.status() != WL_CONNECTED) {
    digitalWrite(PIN_LED_GREEN, !digitalRead(PIN_LED_GREEN)); // Toggle LED
    Serial.print(".");
    vTaskDelay(500 / portTICK_PERIOD_MS);
  }
  
  // Wi-Fi Success!
  digitalWrite(PIN_LED_GREEN, HIGH);
  Serial.println("\nWiFi Connected successfully!");
  Serial.print("ESP32 IP Address: ");
  Serial.println(WiFi.localIP());

  // 3. Setup MQTT
  client.setServer(mqtt_server, 1883);
  unsigned long lastPublish = 0;

  // 4. Main Communication Loop
  for(;;) {
    // Hardware Status Check
    bool has_fault = (!sharedData.ina_ok || !sharedData.bme_ok || !sharedData.mq_ok);
    digitalWrite(PIN_LED_RED, has_fault ? HIGH : LOW);

    // --- CHECK WIFI ---
    if (WiFi.status() != WL_CONNECTED) {
      Serial.println("WiFi lost! Reconnecting...");
      WiFi.reconnect();
      vTaskDelay(2000 / portTICK_PERIOD_MS);
      continue; // Skip the rest of the loop until WiFi is back
    }

    // --- CHECK MQTT ---
    if (!client.connected()) {
      Serial.print("Connecting to MQTT broker at ");
      Serial.print(mqtt_server);
      Serial.print(" ... ");
      
      if (client.connect("CentralSensorNode_01")) {
        Serial.println("CONNECTED!");
        digitalWrite(PIN_LED_GREEN, HIGH);
      } else {
        Serial.print("FAILED, rc=");
        Serial.print(client.state());
        Serial.println(". Retrying in 5 seconds.");
        
        // Blink green LED rapidly to show MQTT failure
        for(int i=0; i<5; i++) {
           digitalWrite(PIN_LED_GREEN, LOW); vTaskDelay(100/portTICK_PERIOD_MS);
           digitalWrite(PIN_LED_GREEN, HIGH); vTaskDelay(100/portTICK_PERIOD_MS);
        }
        vTaskDelay(5000 / portTICK_PERIOD_MS);
        continue; // Skip publishing until connected
      }
    }

    // Keep MQTT connection alive
    client.loop();
    digitalWrite(PIN_LED_GREEN, HIGH); // Solid Green = All good

    // --- PUBLISH DATA (Every 1 Second) ---
    if (millis() - lastPublish > 1000) {
      lastPublish = millis();

      if (xSemaphoreTake(dataMutex, 100) == pdTRUE) {
        if (sharedData.updated) {
          
          // Build JSON Payload
          StaticJsonDocument<512> doc;
          doc["node_id"] = "hvac_monitor_01";
          doc["uptime_s"] = millis() / 1000;

          // Diagnostics / Health
          JsonObject health = doc.createNestedObject("health");
          health["ina219"] = sharedData.ina_ok;
          health["bme280"] = sharedData.bme_ok;
          health["mq135"]  = sharedData.mq_ok;
          health["sys_status"] = has_fault ? "DEGRADED" : "OPTIMAL";

          // Telemetry Data
          JsonObject data = doc.createNestedObject("data");
          data["motor_mA"] = sharedData.current_mA;
          data["motor_V"]  = sharedData.bus_voltage_V;
          data["env_temp_C"] = sharedData.temp_c;
          data["env_hum_RH"] = sharedData.humidity_rh;
          data["env_pres_hPa"] = sharedData.pressure_hPa;
          data["air_qual_raw"] = sharedData.mq135_raw;
          data["air_qual_V"] = sharedData.mq135_volts;

          sharedData.updated = false;
          xSemaphoreGive(dataMutex); // Unlock quickly

          // Publish
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
    
    vTaskDelay(10 / portTICK_PERIOD_MS); // Yield to WiFi Radio
  }
}
