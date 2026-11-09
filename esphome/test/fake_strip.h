#pragma once
#include "esphome.h"
#include <cstdio>
#include <cstdlib>

// Тестовая «лента» в памяти, чтобы прогнать движок на ПК
class FakeStrip : public esphome::light::AddressableLight {
 public:
  static const int N = 264;
  uint8_t buf[N * 3]{};
  uint8_t eff[N]{};
  int32_t size() const override { return N; }
  void clear_effect_data() override {}
  esphome::light::LightTraits get_traits() override {
    auto t = esphome::light::LightTraits();
    t.set_supported_color_modes({esphome::light::ColorMode::RGB});
    return t;
  }
  void write_state(esphome::light::LightState *state) override {}
 protected:
  esphome::light::ESPColorView get_view_internal(int32_t i) const override {
    auto *b = const_cast<uint8_t *>(buf);
    auto *e = const_cast<uint8_t *>(eff);
    return {b + i * 3, b + i * 3 + 1, b + i * 3 + 2, nullptr, e + i, &this->correction_};
  }
};
static FakeStrip fake;

static int step_sum(int s) {
  int t = 0;
  for (int j = 0; j < 24; j++) {
    int i = (s * 24 + j) * 3;
    t += fake.buf[i] + fake.buf[i + 1] + fake.buf[i + 2];
  }
  return t / 24;
}
static void dump(const char *label) {
  printf("%-28s", label);
  for (int s = 0; s < 11; s++) printf("%4d", step_sum(s));
  printf("\n");
  fflush(stdout);
}
