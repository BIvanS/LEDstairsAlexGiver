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
# физическая лента -> простая лампа с тем же id (для управления питанием);
# кадры движка рисует interval ниже в FakeStrip
y = re.sub(r"  # Физическая лента.*?(?=  # То, что видно в HA)", """  - platform: binary
    id: strip
    internal: true
    output: test_strip_out
    effects:
      - lambda:
          name: Engine
          lambda: ''

""", y, flags=re.S)
y = y.replace("\noutput:\n", """
output:
  - platform: template
    id: test_strip_out
    type: binary
    write_action:
      - lambda: ''
""", 1)
# PIR: gpio -> template
y = re.sub(r"  - platform: gpio\n    id: (pir_\w+)\n((?:    .*\n)*?)    pin:\n      number: .*\n      mode: .*\n",
           r"  - platform: template\n    id: \1\n\2", y)
# фоторезистор: adc -> template (напряжение задаёт тест)
y = re.sub(r"  - platform: adc\n    id: ldr_raw\n    pin: .*\n    attenuation: .*\n",
           "  - platform: template\n    id: ldr_raw\n    lambda: 'return id(test_ldr_v);'\n", y)
y = re.sub(r"  - platform: wifi_signal\n(?:    .*\n)+", "", y)
# Управление питанием и расчёт солнца остаются и собираются в тесте.
# Вечернее окно задаёт сам тест, поэтому расчёт по солнцу его не перезаписывает.
y = y.replace("stairs::cur.in_window = win;", "(void) win;")
# полевик: gpio -> template
y = re.sub(r"  - platform: gpio\n    id: strip_power\n    pin:\n(?:      .*\n)+",
           "  - platform: template\n    id: strip_power\n    type: binary\n    write_action:\n      - lambda: ''\n", y)

scenario = r"""
          static uint32_t t0 = millis();
          static int phase = 0;
          static int fails = 0;
          auto check = [&](bool ok, const char *what) {
            printf("  %s %s\n", ok ? "OK  " : "FAIL", what);
            if (!ok) fails++;
          };
          if (phase == 0) { auto nc = id(num_power_hold).make_call(); nc.set_value(5); nc.perform(); phase = 1; id(pir_bottom).publish_state(false); id(pir_top).publish_state(false); }
          uint32_t t = millis() - t0;

          if (phase == 1 && t > 3000) { phase = 2; dump("темно (20 %), ночник");
            check(step_sum(0) > 0 && step_sum(10) > 0 && step_sum(5) == 0, "ночник на крайних ступеньках");
            check(stairs::needs_output() && id(power_on) && id(strip)->remote_values.is_on(), "с ночником полевик и лента включены");
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
            check(!stairs::needs_output(), "днём питание ленты не нужно");
            id(test_ldr_v) = 0.66f; stairs::cur.in_window = false; }
          if (phase == 11 && t > 56500) { phase = 12; id(pir_bottom).publish_state(true); }
          if (phase == 12 && t > 57000) { phase = 13; id(pir_bottom).publish_state(false); dump("темно, но вне вечернего окна");
            check(!stairs::busy() && !stairs::needs_output() && step_sum(0) == 0, "вне окна: нет прохода, ночника и питания");
            check(!id(power_on) && !id(strip)->remote_values.is_on(), "полевик выключен (задержка 5 с прошла)");
            stairs::notify_start("pulse", 0, 0, 255, 50, 2, 0); }
          if (phase == 13 && t > 57500) { phase = 14;
            check(stairs::needs_output() && id(power_on) && id(strip)->remote_values.is_on(), "индикация включает полевик и вне окна");
            printf(fails ? "%d FAILED\n" : "ALL PASSED\n", fails); exit(fails ? 1 : 0); }
"""

# свои globals и interval добавляем в уже существующие списки конфига
y = y.replace("\nglobals:\n", """
globals:
  - id: test_ldr_v
    type: float
    initial_value: '0.66'   # ~26 % от 2.5 В — темно
""", 1)
y = y.replace("\ninterval:\n", """
interval:
  - interval: 20ms
    then:
      - lambda: |-
""" + engine.replace(" " * 12, " " * 10) + scenario, 1)

# Кириллические имена одной длины дают одинаковый object_id (ключ сущности в HA)
ids, cur = {}, None
for line in src.splitlines():
    m = re.match(r"^([a-z_]+):\s*$", line)
    if m:
        cur = m.group(1)
    m = re.match(r'^\s+name:\s*"(.*)"', line)
    if m and cur:
        n = m.group(1)
        oid = "".join(c if c.isascii() and (c.isalnum() or c in "_-") else "_" for c in n.lower().replace(" ", "_"))
        if (cur, oid) in ids:
            raise SystemExit(f"Совпадают идентификаторы в {cur}: «{ids[(cur, oid)]}» и «{n}» — поменяй длину имени")
        ids[(cur, oid)] = n

(OUT / "stairs-test.yaml").write_text(y, encoding="utf-8")
print(f"Готово: {OUT / 'stairs-test.yaml'}")
