"""
Тест логики лестницы на ПК (платформа ESPHome `host`, без железа).

Берёт настоящий ../stairs-led.yaml, заменяет железо на заглушки
(ленту — на FakeStrip в памяти, PIR и фоторезистор — на template-сущности),
а лямбду движка копирует без изменений. Затем прогоняет сценарий и
проверяет результат.

Запуск (нужен Linux или WSL, ESPHome в venv):
    python3 make_test.py            # создаёт build/stairs-test.yaml
    esphome compile build/stairs-test.yaml
    ./build/.esphome/build/stairs-test/.pioenvs/stairs-test/program
В конце печатается ALL PASSED или список ошибок (код выхода 1).
"""
import pathlib
import re
import shutil

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE.parent / "stairs-led.yaml"
OUT = HERE / "build"
OUT.mkdir(exist_ok=True)
shutil.copy(HERE.parent / "stairs_logic.h", OUT)
shutil.copy(HERE / "fake_strip.h", OUT)

src = SRC.read_text(encoding="utf-8")

# Тело лямбды движка — из реального конфига
m = re.search(r"name: Engine\n\s+update_interval: 20ms\n\s+lambda: \|-\n((?: {12}.*\n)+)", src)
if not m:
    raise SystemExit("Не найден эффект Engine в stairs-led.yaml")
engine = m.group(1).replace("stairs::render(it);", "stairs::render(fake);")

# Всё после logger: — общее; шапку заменяем на host
y = src[src.index("\nlogger:") + len("\nlogger:"):]
subs = re.search(r"substitutions:\n(?: {2}.*\n)+", src).group(0)
y = subs + """esphome:
  name: stairs-test
  includes:
    - stairs_logic.h
    - fake_strip.h
host:
logger:""" + y

y = re.sub(r"ota:\n(?: {2,}.*\n)+", "", y)
y = re.sub(r"wifi:\n(?: {2,}.*\n)+", "", y)
y = y.replace("captive_portal:\n", "")
y = re.sub(r"(?:# .*\n)?status_led:\n(?: {2}.*\n)+", "", y)
# веб-интерфейс на платформе host не поддерживается — убираем
y = re.sub(r"^(?:# .*\n)*web_server:\n(?: {2,}.*\n)+", "", y, flags=re.M)
y = re.sub(r"^( +)web_server:\n(?:\1  .*\n)+", "", y, flags=re.M)
y = y.replace("encryption:\n    key: !secret stairs_api_key\n", "")
# физическая лента — убираем, кадры рисует interval ниже
y = re.sub(r"  # Физическая лента.*?(?=  # То, что видно в HA)", "", y, flags=re.S)
# PIR: gpio -> template
y = re.sub(r"  - platform: gpio\n    id: (pir_\w+)\n((?:    .*\n)*?)    pin:\n      number: .*\n      mode: .*\n",
           r"  - platform: template\n    id: \1\n\2", y)
# фоторезистор: adc -> template (напряжение задаёт тест)
y = re.sub(r"  - platform: adc\n    id: ldr_raw\n    pin: .*\n    attenuation: .*\n",
           "  - platform: template\n    id: ldr_raw\n    lambda: 'return id(test_ldr_v);'\n", y)
y = re.sub(r"  - platform: wifi_signal\n(?:    .*\n)+", "", y)

scenario = r"""
          static uint32_t t0 = millis();
          static int phase = 0;
          static int fails = 0;
          auto check = [&](bool ok, const char *what) {
            printf("  %s %s\n", ok ? "OK  " : "FAIL", what);
            if (!ok) fails++;
          };
          if (phase == 0) { phase = 1; id(pir_bottom).publish_state(false); id(pir_top).publish_state(false); }
          uint32_t t = millis() - t0;

          if (phase == 1 && t > 3000) { phase = 2; dump("темно (20 %), ночник");
            check(step_sum(0) > 0 && step_sum(10) > 0 && step_sum(5) == 0, "ночник на крайних ступеньках");
            id(pir_bottom).publish_state(true); }
          if (phase == 2 && t > 3300) { phase = 3; id(pir_bottom).publish_state(false); dump("вверх +300 мс");
            check(step_sum(1) > step_sum(3), "волна идёт снизу"); }
          if (phase == 3 && t > 6000) { phase = 4; dump("вверх, всё горит");
            bool all = true; for (int s = 0; s < 11; s++) if (step_sum(s) == 0) all = false;
            check(all && stairs::busy(), "горят все ступеньки"); }
          if (phase == 4 && t > 25000) { phase = 5; dump("после таймаута");
            check(!stairs::busy() && step_sum(5) == 0 && step_sum(0) > 0, "погасло, остался ночник");
            stairs::notify_start("level", 0, 255, 0, 100, 3, 45); }
          if (phase == 5 && t > 25500) { phase = 6; dump("индикация level 45 %");
            check(step_sum(3) > 0 && step_sum(4) > 0 && step_sum(4) < step_sum(3) && step_sum(5) == 0, "шкала 45 %");
            stairs::notify_start("steps", 255, 0, 0, 100, 0, 1025); }
          if (phase == 6 && t > 26000) { phase = 7; dump("индикация steps 1025");
            check(step_sum(0) > 0 && step_sum(10) > 0 && step_sum(5) == 0, "маска: нижняя и верхняя");
            id(pir_top).publish_state(true); }
          if (phase == 7 && t > 26500) { phase = 8; id(pir_top).publish_state(false); dump("вниз +500 мс поверх индикации");
            check(step_sum(9) > 0 && step_sum(2) == 0, "волна идёт сверху"); }
          if (phase == 8 && t > 29000) { phase = 9; dump("вниз, всё горит"); id(test_ldr_v) = 2.5f; }
          if (phase == 9 && t > 50000) { phase = 10; dump("светло, после таймаута");
            check(stairs::notify_name() == "steps" && step_sum(0) > 0, "индикация осталась и днём");
            stairs::notify_stop(); id(pir_bottom).publish_state(true); }
          if (phase == 10 && t > 51500) { phase = 11; id(pir_bottom).publish_state(false); dump("движение в светлой комнате");
            check(!stairs::busy() && step_sum(0) == 0, "днём по движению не включается");
            printf(fails ? "%d FAILED\n" : "ALL PASSED\n", fails); exit(fails ? 1 : 0); }
"""

y += """
globals:
  - id: test_ldr_v
    type: float
    initial_value: '0.66'   # 20 % от 3.3 В — темно

interval:
  - interval: 20ms
    then:
      - lambda: |-
""" + engine.replace(" " * 12, " " * 10) + scenario

(OUT / "stairs-test.yaml").write_text(y, encoding="utf-8")
print(f"Готово: {OUT / 'stairs-test.yaml'}")
