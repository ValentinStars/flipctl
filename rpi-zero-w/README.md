# FlipCTL for Raspberry Pi Zero W & Waveshare 1.44inch LCD HAT

Руководство по запуску и настройке интерфейса **FlipCTL** на микрокомпьютере **Raspberry Pi Zero W** с дисплеем **Waveshare 1.44inch LCD HAT** (ST7735S 128x128 SPI + 8 кнопок/джойстик).

> **Особенность**: Решение готово «из коробки» (**Zero Compilation**). Ничего не нужно компилировать на слабом одноядерном ARMv6 процессоре Pi Zero W!

---

## 1. Спецификация оборудования

- **Плата**: Raspberry Pi Zero W (BCM2835, 1GHz ARMv6, 512MB RAM)
- **Шляпа/Экран**: [Waveshare 1.44inch LCD HAT](https://www.waveshare.com/wiki/1.44inch_LCD_HAT)
- **Контроллер экрана**: ST7735S (128×128, 65K цветов, SPI)
- **Пины SPI**:
  - `MOSI` -> GPIO 10 (Pin 19)
  - `SCLK` -> GPIO 11 (Pin 23)
  - `CE0` -> GPIO 8 (Pin 24)
  - `DC` (Data/Command) -> GPIO 25 (Pin 22)
  - `RST` (Reset) -> GPIO 27 (Pin 13)
  - `BL` (Backlight) -> GPIO 24 (Pin 18)
- **Кнопки и Джойстик** (Active LOW, подтяжка PULL_UP):
  - `Key 1` (OK / Выбор) -> GPIO 21 (Pin 40)
  - `Key 2` (Back / Назад) -> GPIO 20 (Pin 38)
  - `Key 3` (Menu / Меню) -> GPIO 16 (Pin 36)
  - `Джойстик Вверх` -> GPIO 6 (Pin 31)
  - `Джойстик Вниз` -> GPIO 19 (Pin 35)
  - `Джойстик Влево` -> GPIO 5 (Pin 29)
  - `Джойстик Вправо` -> GPIO 26 (Pin 37)
  - `Джойстик Нажатие` -> GPIO 13 (Pin 33)

---

## 2. Быстрая установка (1 команда)

На вашей Raspberry Pi Zero W (с Raspberry Pi OS Lite):

```bash
git clone https://github.com/ValentinStars/flipctl.git
cd flipctl/rpi-zero-w
sudo bash install.sh
```

Скрипт автоматически:
1. Установит необходимые стандартные библиотеки (`python3-pil`, `python3-spidev`, `python3-rpi.gpio`).
2. Включит аппаратный интерфейс SPI в ядре.
3. Настроит и включит фоновый `systemd` сервис `flipctl-st7735.service`.

---

## 3. Режимы работы

### Вариант А: Сетевой компаньон (Рекомендуемый для Zero W)
В этом режиме FlipCTL запускается на вашем основном ноутбуке/ПК, а Raspberry Pi Zero W выступает в роли физического карманного пульта с экраном и кнопками, соединяясь по WiFi!
- Мгновенная отрисовка 256x144, отмасштабированная под 128x128 дисплей ST7735 в фирменном янтарном цвете Flipper Amber (`#FF8200`).
- Нажатия джойстика и кнопок мгновенно передаются на сервер FlipCTL.
- Процессор Pi Zero W нагружен менее чем на 10%.

Чтобы настроить адрес ноутбука, отредактируйте сервис:
```bash
sudo nano /etc/systemd/system/flipctl-st7735.service
# Измените --host 127.0.0.1 на IP ноутбука, например --host 192.168.1.50
sudo systemctl daemon-reload
sudo systemctl restart flipctl-st7735
```

### Вариант Б: Полностью автономный (Stand-alone на Pi Zero W)
Если вы запускаете фоновый демон `flipctl` локально на самой Pi Zero W:
- Сервис по умолчанию подключается к `127.0.0.1:8899`.
- Экран и кнопки работают напрямую с локальным демоном.

---

## 4. Аппаратный драйвер ядра (Linux Framebuffer /dev/fb1)

Если вы хотите использовать родной драйвер ядра Linux `fbtft`:
1. Скопируйте строки из файла `config.txt.snippet` в конец `/boot/firmware/config.txt` (или `/boot/config.txt` на старых версиях ОС).
2. Перезагрузите плату:
   ```bash
   sudo reboot
   ```
3. В системе появится устройство `/dev/fb1` (128x128 16-bit RGB565) и виртуальная клавиатура `gpio-keys`.

---

## 5. Управление сервисом

```bash
# Проверить статус
sudo systemctl status flipctl-st7735

# Перезапустить
sudo systemctl restart flipctl-st7735

# Посмотреть живой лог
journalctl -u flipctl-st7735 -f
```
