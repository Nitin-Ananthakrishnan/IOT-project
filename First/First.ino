#include <WiFi.h>
#include <PubSubClient.h>
#include <Wire.h>
#include <Adafruit_INA219.h>
#include <Adafruit_BME280.h>
#include <ArduinoJson.h>

// ==========================================
// --- CONFIGURATION ---
// ==========================================
const char* ssid = " ";               
const char* password = " ";        
const char* mqtt_server = " "; 
const char* mqtt_topic = "telemetry/room1/sensors";

// --- PINS ---
#define PIN_SDA 21
#define PIN_SCL 22
#define PIN_MQ135 34
#define PIN_NTC 35        // NEW: Motor Temperature Sensor
#define PIN_LED_GREEN 4   
#define PIN_LED_RED 15    
#define PIN_LED_TRIP 13   

// --- SAFETY THRESHOLDS ---
#define TRIP_CURRENT_mA 3000.0 // 3 Amps - Critical overcurrent
#define TRIP_TEMP_C     80.0   // 80°C - Critical motor overheat
#define NUM_INA_SAMPLES 20     

// --- NTC THERMISTOR CONFIGURATION ---
#define NTC_SERIES_RESISTOR 10000.0 // 10K ohms fixed resistor
#define NTC_NOMINAL_RESIST  10000.0 // 10K ohms at 25 degrees C
#define NTC_NOMINAL_TEMP    25.0    // Nominal temperature
#define NTC_B_COEFFICIENT   3950.0  // Beta coefficient of the thermistor (common is 3950)

// ==========================================
// --- SHARED DATA STRUCTURE & RTOS ---
// ==========================================
struct SensorData {
  float current_mA;
  float motor_temp_c; // NEW: NTC Temperature
  float env_temp_c;
  float pressure_hPa;
  float humidity_rh;
  int   mq135_raw;
  float mq135_volts;
  bool  ina_ok;
  bool  bme_ok;
  bool  mq_ok;
  bool  ntc_ok;       // NEW: Health check for NTC
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
  
  pinMode(PIN_MQ135, INPUT);
  pinMode(PIN_NTC, INPUT); // NEW: Set NTC pin
  pinMode(PIN_LED_GREEN, OUTPUT);
  pinMode(PIN_LED_RED, OUTPUT);
  pinMode(PIN_LED_TRIP, OUTPUT);
  
  sharedData.is_tripped = false;

  digitalWrite(PIN_LED_GREEN, HIGH);
  digitalWrite(PIN_LED_RED, HIGH);
  digitalWrite(PIN_LED_TRIP, HIGH);

  dataMutex = xSemaphoreCreateMutex();
  Wire.begin(PIN_SDA, PIN_SCL);
  delay(500);

  Serial.println("\n--- SYSTEM BOOT: HEALTH CHECKS ---");
  
  if (!ina219.begin()) { Serial.println("FAULT: INA219 not found!"); sharedData.ina_ok = false; } 
  else { Serial.println("OK: INA219 Ready."); sharedData.ina_ok = true; }

  if (!bme.begin(0x76) && !bme.begin(0x77)) { Serial.println("FAULT: BME280 not found!"); sharedData.bme_ok = false; } 
  else { Serial.println("OK: BME280 Ready."); sharedData.bme_ok = true; }
  
  sharedData.mq_ok = true; 
  sharedData.ntc_ok = true; 

  digitalWrite(PIN_LED_GREEN, LOW);
  digitalWrite(PIN_LED_RED, LOW);
  digitalWrite(PIN_LED_TRIP, LOW);

  Serial.println("Starting Core 1 (Sensors)...");
  xTaskCreatePinnedToCore(taskSensorsCode, "Sensors", 10000, NULL, 1, &TaskSensors, 1);
  delay(500);
  Serial.println("Starting Core 0 (Comms)...");
  xTaskCreatePinnedToCore(taskCommsCode, "Comms", 16000, NULL, 1, &TaskComms, 0);
}

void loop() { vTaskDelete(NULL); }

// ==========================================
// --- CORE 1: SENSING & FAULT DETECTION ---
// ==========================================
void taskSensorsCode(void * parameter) {
  for(;;) {
    if (sharedData.is_tripped) {
      vTaskDelay(1000 / portTICK_PERIOD_MS);
      continue;
    }

    float temp_cur = 0, env_t = 0, p_hpa = 0, h_rh = 0;
    int mq_raw = 0;
    float ntc_temp_c = 0;
    bool local_ntc_ok = true;
    
    // 1. Read INA219
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
      env_t = round2Decimals(bme.readTemperature());
      p_hpa = round2Decimals(bme.readPressure() / 100.0F);
      h_rh = round2Decimals(bme.readHumidity());
    }

    // 3. Read MQ-135
    long mq_sum = 0;
    for(int i = 0; i < 10; i++) { mq_sum += analogRead(PIN_MQ135); delay(2); }
    mq_raw = mq_sum / 10;
    float mq_volts = round2Decimals((mq_raw / 4095.0) * 3.3 * 2.0); 
    bool local_mq_ok = (mq_raw > 20 && mq_raw < 3200);

    // 4. Read NTC Thermistor (Steinhart-Hart Equation)
    int ntc_raw = analogRead(PIN_NTC);
    if (ntc_raw < 10 || ntc_raw > 4085) {
      local_ntc_ok = false; // Wire cut or shorted
    } else {
      // Convert raw ADC to resistance
      float ntc_resistance = NTC_SERIES_RESISTOR * ((4095.0 / ntc_raw) - 1.0);
      
      // Calculate Temperature in Kelvin
      float steinhart;
      steinhart = ntc_resistance / NTC_NOMINAL_RESIST;      // (R/Ro)
      steinhart = log(steinhart);                           // ln(R/Ro)
      steinhart /= NTC_B_COEFFICIENT;                       // 1/B * ln(R/Ro)
      steinhart += 1.0 / (NTC_NOMINAL_TEMP + 273.15);       // + (1/To)
      steinhart = 1.0 / steinhart;                          // Invert
      
      ntc_temp_c = round2Decimals(steinhart - 273.15);      // Convert to Celsius
    }
    
    // 5. Check for Critical Trip (Overcurrent OR Motor Overheat)
    bool tripped_now = (temp_cur > TRIP_CURRENT_mA) || (ntc_temp_c > TRIP_TEMP_C);
    
    // 6. Lock & Update Shared Memory
    if (xSemaphoreTake(dataMutex, portMAX_DELAY) == pdTRUE) {
      sharedData.current_mA = temp_cur;
      sharedData.env_temp_c = env_t;
      sharedData.motor_temp_c = ntc_temp_c; // Store motor temp
      sharedData.pressure_hPa = p_hpa;
      sharedData.humidity_rh = h_rh;
      sharedData.mq135_raw = mq_raw;
      sharedData.mq135_volts = mq_volts;
      
      sharedData.mq_ok = local_mq_ok;
      sharedData.ntc_ok = local_ntc_ok; // Store NTC health
      
      if (tripped_now) sharedData.is_tripped = true; 
      
      sharedData.updated = true;
      xSemaphoreGive(dataMutex);
    }

    vTaskDelay(100 / portTICK_PERIOD_MS); 
  }
}

// ==========================================
// --- CORE 0: WIFI, MQTT & LED LOGIC ---
// ==========================================
void taskCommsCode(void * parameter) {
  WiFi.mode(WIFI_STA);
  client.setServer(mqtt_server, 1883);
  
  unsigned long lastPublish = 0;
  bool is_connecting_wifi = false;

  for(;;) {
    bool has_fault = false;
    bool is_tripped = false;
    if (xSemaphoreTake(dataMutex, 10) == pdTRUE) {
      // NEW: Added ntc_ok to the fault logic
      has_fault = (!sharedData.ina_ok || !sharedData.bme_ok || !sharedData.mq_ok || !sharedData.ntc_ok);
      is_tripped = sharedData.is_tripped;
      xSemaphoreGive(dataMutex);
    }

    digitalWrite(PIN_LED_TRIP, is_tripped ? HIGH : LOW);
    digitalWrite(PIN_LED_RED, (has_fault && !is_tripped) ? HIGH : LOW);

    if (WiFi.status() != WL_CONNECTED) {
      digitalWrite(PIN_LED_GREEN, millis() % 1000 < 500); 
      if (!is_connecting_wifi) {
        WiFi.disconnect();
        delay(100);
        WiFi.begin(ssid, password);
        is_connecting_wifi = true;
      }
    } 
    else {
      is_connecting_wifi = false; 
      if (!client.connected()) {
        digitalWrite(PIN_LED_GREEN, millis() % 200 < 100); 
        if (client.connect("CentralSensorNode_01", "esp32_device", "esp32pass")) { 
          // Connected! (Using the credentials from your Linux setup)
        } else {
          vTaskDelay(3000 / portTICK_PERIOD_MS); 
        }
      } 
      else {
        digitalWrite(PIN_LED_GREEN, HIGH); 
        client.loop(); 
        
        if (millis() - lastPublish > 1000) {
          lastPublish = millis();
          
          if (xSemaphoreTake(dataMutex, 100) == pdTRUE) {
            if (sharedData.updated) {
              
              StaticJsonDocument<512> doc;
              doc["node_id"] = "hvac_monitor_01";
              doc["uptime_s"] = millis() / 1000;

              JsonObject health = doc.createNestedObject("health");
              health["ina219"] = sharedData.ina_ok;
              health["bme280"] = sharedData.bme_ok;
              health["mq135"]  = sharedData.mq_ok;
              health["ntc"]    = sharedData.ntc_ok; // Added to payload
              health["trip_status"] = sharedData.is_tripped;
              
              if (sharedData.is_tripped) health["sys_status"] = "TRIPPED";
              else if (has_fault)        health["sys_status"] = "DEGRADED";
              else                       health["sys_status"] = "OPTIMAL";

              JsonObject data = doc.createNestedObject("data");
              data["motor_mA"]   = sharedData.current_mA;
              data["motor_temp_C"] = sharedData.motor_temp_c; // Added to payload
              data["env_temp_C"] = sharedData.env_temp_c;
              data["env_hum_RH"] = sharedData.humidity_rh;
              data["env_pres_hPa"]= sharedData.pressure_hPa;
              data["air_qual_raw"]= sharedData.mq135_raw;
              data["air_qual_V"]  = sharedData.mq135_volts;

              sharedData.updated = false;
              xSemaphoreGive(dataMutex);

              char buffer[512];
              serializeJson(doc, buffer);
              client.publish(mqtt_topic, buffer);
              Serial.println(buffer);
            } else {
              xSemaphoreGive(dataMutex);
            }
          }
        }
      }
    }
    vTaskDelay(50 / portTICK_PERIOD_MS); 
  }
}
