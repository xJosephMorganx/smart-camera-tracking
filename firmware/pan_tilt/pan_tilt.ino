#include <Servo.h>

Servo pan;
Servo tilt;

const int PAN_PIN = 9;
const int TILT_PIN = 10;
const int PAN_HOME = 80;
const int TILT_HOME = 100;
const int HOME_STEP = 2;

int panPos = PAN_HOME;
int tiltPos = TILT_HOME;

int approach(int current, int target, int step) {
  if (current < target) return min(current + step, target);
  if (current > target) return max(current - step, target);
  return current;
}

void setup() {
  Serial.begin(115200);
  pan.attach(PAN_PIN);
  tilt.attach(TILT_PIN);
  pan.write(panPos);
  tilt.write(tiltPos);
}

void loop() {
  if (!Serial.available()) return;

  String message = Serial.readStringUntil('\n');
  message.trim();

  // Each H command takes one step toward home; the app repeats it.
  if (message == "H") {
    panPos = approach(panPos, PAN_HOME, HOME_STEP);
    tiltPos = approach(tiltPos, TILT_HOME, HOME_STEP);
    pan.write(panPos);
    tilt.write(tiltPos);
    return;
  }

  // Relative dx,dy corrections preserve the tested servo directions.
  int comma = message.indexOf(',');
  if (comma > 0) {
    int dx = message.substring(0, comma).toInt();
    int dy = message.substring(comma + 1).toInt();
    panPos -= dx;
    tiltPos += dy;
    panPos = constrain(panPos, 0, 180);
    tiltPos = constrain(tiltPos, 0, 180);
    pan.write(panPos);
    tilt.write(tiltPos);
  }
}
