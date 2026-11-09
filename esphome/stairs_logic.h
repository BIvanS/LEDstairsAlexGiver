// =====================================================================
//  Подсветка лестницы — логика для ESPHome
//  Перенос проекта AlexGyver LEDstairs (Arduino Nano) на ESP32/ESP8266.
//  Вся логика работает на самом контроллере — без HA, Wi-Fi и брокера.
//
//  Слои отрисовки (сверху вниз по приоритету):
//    1. Проход по лестнице  — волна по ступенькам (безопасность важнее всего)
//    2. Индикация           — сигналы из HA / Node-RED (action notify)
//    3. Ночная подсветка    — крайние ступеньки, когда темно
//    4. Темнота
// =====================================================================
#pragma once
#include "esphome.h"
#include <cmath>
#include <string>

namespace stairs {

using esphome::Color;
using esphome::light::AddressableLight;
using esphome::light::ESPHSVColor;

// ---------- Геометрия (поменять тут, если изменится лестница) ----------
static const int STEPS = 11;           // ступенек
static const int LEDS_PER_STEP = 24;   // светодиодов на ступеньке
static const int NUM_LEDS = STEPS * LEDS_PER_STEP;  // 264
// Ступенька 0 — нижняя («начало лестницы»), лента начинается снизу.

// ---------- Настройки, которые YAML передаёт каждый кадр ----------
struct Settings {
  bool enabled = true;        // главный свет «Лестница» включён
  bool auto_motion = true;    // реагировать на датчики
  bool night = true;          // ночная подсветка
  bool auto_bright = true;    // автояркость по фоторезистору
  bool sensor_high = false;   // один из PIR сейчас в состоянии «движение»
  Color color = Color(255, 255, 255);
  float bright = 1.0f;        // яркость основного света 0..1
  float light_pct = 0.0f;     // освещённость с фоторезистора, %
  float dark_threshold = 50;  // порог темноты, %
  float night_bright = 0.5f;  // яркость ночной подсветки 0..1
  uint8_t effect_mode = 0;    // 0 цвет, 1 радуга, 2 огонь, 3 по очереди
  uint32_t timeout_ms = 15000;
  uint32_t step_delay_ms = 150;
  uint32_t fade_ms = 400;
};
static Settings cur;

// ---------- Состояние прохода ----------
static float level[STEPS];          // текущая яркость ступеньки 0..1
static int8_t target[STEPS];        // куда стремится
static uint32_t sched_at[STEPS];    // когда сменить цель
static int8_t sched_target[STEPS];  // на что сменить (-1 — ничего)
static bool walk_on = false;
static int8_t direction = 1;        // +1 вверх, -1 вниз
static uint8_t walk_effect = 0;
static uint8_t rotate_counter = 0;
static uint32_t last_motion = 0;
static uint32_t last_frame = 0;
static float hue_pos = 0;
static uint8_t heat[NUM_LEDS];
static bool is_dark = true;
static bool initialized = false;

// ---------- Ночная подсветка ----------
static uint32_t night_mask = 0b100100100100100100100100;  // каждый 3-й диод
static uint32_t last_night_shift = 0;

// ---------- Индикация ----------
enum Pattern : uint8_t { P_SOLID, P_PULSE, P_BLINK, P_ALARM, P_RUN_UP, P_RUN_DOWN, P_LEVEL, P_STEPS, P_COUNT };
static const char *const PATTERN_NAMES[P_COUNT] = {"solid", "pulse", "blink", "alarm",
                                                    "run_up", "run_down", "level", "steps"};
struct Notify {
  bool active = false;
  Pattern pattern = P_PULSE;
  Color color = Color(0, 0, 255);
  float bright = 0.6f;
  uint32_t start = 0;
  uint32_t until = 0;   // 0 — пока не остановят
  int32_t value = 0;    // level: 0..100 %, steps: битовая маска ступенек
};
static Notify ntf;

// ---------- Вспомогательное ----------
static inline float clamp01(float v) { return v < 0 ? 0 : (v > 1 ? 1 : v); }
static inline Color scale(const Color &c, float k) {
  k = clamp01(k);
  return Color((uint8_t) (c.r * k), (uint8_t) (c.g * k), (uint8_t) (c.b * k));
}
static inline Color mix(const Color &a, const Color &b, float t) {
  t = clamp01(t);
  return Color((uint8_t) (a.r + (b.r - a.r) * t), (uint8_t) (a.g + (b.g - a.g) * t),
               (uint8_t) (a.b + (b.b - a.b) * t));
}
static inline int led_index(int step, int j) { return step * LEDS_PER_STEP + j; }

static void init() {
  for (int i = 0; i < STEPS; i++) {
    level[i] = 0;
    target[i] = 0;
    sched_target[i] = -1;
  }
  for (int i = 0; i < NUM_LEDS; i++) heat[i] = 150;
  initialized = true;
}

// Запланировать волну: включить (1) или выключить (0) ступеньки по очереди
static void schedule(int8_t tgt, uint32_t now) {
  for (int k = 0; k < STEPS; k++) {
    int idx = direction > 0 ? k : STEPS - 1 - k;
    sched_at[idx] = now + (uint32_t) k * cur.step_delay_ms;
    sched_target[idx] = tgt;
  }
}

// ---------- Публичные функции (вызываются из YAML) ----------

// Срабатывание датчика. dir: +1 — нижний датчик (идут вверх), -1 — верхний.
// force — для кнопки «тест», игнорирует освещённость.
// Возвращает true, если начался новый проход (для события в HA).
static bool motion(int8_t dir, bool force = false) {
  if (!initialized) init();
  uint32_t now = millis();
  last_motion = now;
  bool allowed = force || (cur.enabled && cur.auto_motion && is_dark);
  if (!allowed || walk_on) return false;
  direction = dir;
  if (cur.effect_mode == 3) {
    walk_effect = rotate_counter++ % 3;
  } else {
    walk_effect = cur.effect_mode;
  }
  walk_on = true;
  schedule(1, now);
  return true;
}

static bool busy() { return walk_on; }
static const char *direction_name() { return direction > 0 ? "up" : "down"; }

static void notify_start(const std::string &pattern, int r, int g, int b, int brightness, int duration_s,
                         int value) {
  Pattern p = P_PULSE;
  for (int i = 0; i < P_COUNT; i++)
    if (pattern == PATTERN_NAMES[i]) p = (Pattern) i;
  ntf.pattern = p;
  ntf.color = Color((uint8_t) r, (uint8_t) g, (uint8_t) b);
  ntf.bright = clamp01(brightness / 100.0f);
  ntf.value = value;
  ntf.start = millis();
  ntf.until = duration_s > 0 ? ntf.start + (uint32_t) duration_s * 1000UL : 0;
  ntf.active = true;
}
static void notify_stop() { ntf.active = false; }
static std::string notify_name() { return ntf.active ? PATTERN_NAMES[ntf.pattern] : "none"; }

// ---------- Эффекты прохода ----------
static Color fire_color(uint8_t h) {
  // от тёмно-красного через оранжевый к жёлтому
  uint8_t r = 255;
  uint8_t g = h < 128 ? h / 3 : (uint8_t) std::min(200, 42 + (h - 128) * 5 / 4);
  uint8_t b = h > 230 ? (uint8_t) ((h - 230) * 2) : 0;
  return scale(Color(r, g, b), 0.35f + 0.65f * h / 255.0f);
}

static Color walk_color(int step, int j) {
  switch (walk_effect) {
    case 1: {  // радуга: у каждой ступеньки свой цвет, цвета плывут по направлению движения
      uint8_t hue = (uint8_t) ((int) hue_pos + step * 256 / STEPS);
      return ESPHSVColor(hue, 255, 255).to_rgb();
    }
    case 2:  // огонь
      return fire_color(heat[led_index(step, j)]);
    default:  // цвет из HA
      return cur.color;
  }
}

static void update_fire() {
  for (int i = 0; i < NUM_LEDS; i++) {
    int tgt = 90 + (int) (esphome::random_uint32() % 166);
    heat[i] = (uint8_t) (heat[i] + (tgt - heat[i]) / 5);
  }
}

// ---------- Индикация ----------
static Color notify_color(int step, int j, uint32_t now) {
  uint32_t t = now - ntf.start;
  Color c = scale(ntf.color, ntf.bright);
  switch (ntf.pattern) {
    case P_SOLID:
      return c;
    case P_PULSE: {  // плавное «дыхание» всей лестницы
      float f = 0.5f - 0.5f * cosf(2.0f * (float) M_PI * (t % 2000) / 2000.0f);
      return scale(c, 0.1f + 0.9f * f);
    }
    case P_BLINK:
      return ((t / 400) % 2) ? Color(0, 0, 0) : c;
    case P_ALARM: {  // красный/синий через ступеньку, меняются местами
      bool phase = ((t / 300) % 2) ^ (step % 2);
      return scale(phase ? Color(255, 0, 0) : Color(0, 0, 255), ntf.bright);
    }
    case P_RUN_UP:
    case P_RUN_DOWN: {  // бегущая «комета» по ступенькам
      const float tail = 4.0f;
      float head = fmodf(t / 110.0f, STEPS + tail);
      int s = ntf.pattern == P_RUN_UP ? step : STEPS - 1 - step;
      float d = head - s;
      if (d < 0 || d >= tail) return Color(0, 0, 0);
      return scale(c, 1.0f - d / tail);
    }
    case P_LEVEL: {  // шкала 0..100 % снизу вверх, последняя ступенька заполняется по ширине
      float filled = clamp01(ntf.value / 100.0f) * STEPS;
      if (step < (int) filled) return c;
      if (step == (int) filled) {
        float frac = filled - (int) filled;
        return (j < (int) (frac * LEDS_PER_STEP + 0.5f)) ? c : Color(0, 0, 0);
      }
      return Color(0, 0, 0);
    }
    case P_STEPS:  // конкретные ступеньки: бит 0 — нижняя
      return (ntf.value >> step) & 1 ? c : Color(0, 0, 0);
    default:
      return Color(0, 0, 0);
  }
}

// ---------- Ночная подсветка ----------
static Color night_color(int step, int j) {
  if (step != 0 && step != STEPS - 1) return Color(0, 0, 0);
  if (!((night_mask >> j) & 1)) return Color(0, 0, 0);
  return scale(Color(255, 255, 255), cur.night_bright);
}

// ---------- Главный кадр (вызывается эффектом addressable_lambda) ----------
static void render(AddressableLight &it) {
  if (!initialized) init();
  uint32_t now = millis();
  uint32_t dt = now - last_frame;
  last_frame = now;
  if (dt > 100) dt = 100;

  // Темно/светло с гистерезисом 5 %
  if (is_dark && cur.light_pct > cur.dark_threshold + 5) is_dark = false;
  else if (!is_dark && cur.light_pct < cur.dark_threshold) is_dark = true;

  // Человек всё ещё стоит под датчиком — продлеваем
  if (walk_on && cur.sensor_high) last_motion = now;

  // Таймаут или выключили — гасим волной
  if (walk_on && (now - last_motion >= cur.timeout_ms || !cur.enabled)) {
    walk_on = false;
    schedule(0, now);
  }

  // Плановые переключения и плавное изменение яркости ступенек
  float stepv = cur.fade_ms > 0 ? (float) dt / cur.fade_ms : 1.0f;
  bool any_level = false;
  for (int s = 0; s < STEPS; s++) {
    if (sched_target[s] >= 0 && (int32_t) (now - sched_at[s]) >= 0) {
      target[s] = sched_target[s];
      sched_target[s] = -1;
    }
    if (target[s] && level[s] < 1) level[s] = std::min(1.0f, level[s] + stepv);
    if (!target[s] && level[s] > 0) level[s] = std::max(0.0f, level[s] - stepv);
    if (level[s] > 0) any_level = true;
  }

  // Анимации
  hue_pos += dt * 0.06f * direction;
  if (hue_pos > 256) hue_pos -= 256;
  if (hue_pos < 0) hue_pos += 256;
  if (any_level && walk_effect == 2) update_fire();
  if (now - last_night_shift >= 60000) {  // сдвиг маски раз в минуту, чтобы диоды не выгорали
    last_night_shift = now;
    night_mask = ((night_mask >> 1) | ((night_mask & 1) << (LEDS_PER_STEP - 1))) & 0xFFFFFF;
  }

  // Срок индикации истёк
  if (ntf.active && ntf.until && (int32_t) (now - ntf.until) >= 0) ntf.active = false;

  // Яркость прохода (автояркость: темнее в комнате — тусклее лестница, как в оригинале)
  float walk_bright = cur.bright;
  if (cur.auto_bright && cur.dark_threshold > 0)
    walk_bright *= 0.3f + 0.7f * clamp01(cur.light_pct / cur.dark_threshold);

  bool show_night = cur.enabled && cur.night && is_dark;

  for (int s = 0; s < STEPS; s++) {
    for (int j = 0; j < LEDS_PER_STEP; j++) {
      Color bg(0, 0, 0);
      if (ntf.active) bg = notify_color(s, j, now);
      else if (show_night) bg = night_color(s, j);
      Color out = bg;
      if (level[s] > 0) out = mix(bg, scale(walk_color(s, j), walk_bright), level[s]);
      it[led_index(s, j)] = out;
    }
  }
}

}  // namespace stairs
