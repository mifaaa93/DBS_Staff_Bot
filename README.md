# DBS Staff Bot

Telegram-бот для автосервиса DBS: автоматические напоминания в топики группы и
автоматический подбор «дежурных двоек» (мусор, социалка) из списка сотрудников.

## Возможности

- Расписание уведомлений (часовой пояс — `TIMEZONE`, по умолчанию Europe/Prague):
  - **Мусор** — Пн 17:45 (двойка).
  - **Социалка** — Ср 09:00 / 16:45 / 17:00 (одна двойка на день, три текста).
  - **Генералка** — Пт 15:00 (общее уведомление, без двойки; текст-плейсхолдер).
- Двойки случайные, но «как в Apple Music»: никто не дежурит два раза подряд по своей
  задаче, реже дежурившие выпадают чаще. Двойка формируется в день уведомления.
- Управление сотрудниками через бота (только админы): добавить кнопкой «выбрать
  пользователя», удалить из списка. Увольнение мягкое — история дежурств сохраняется.
- Если активных сотрудников меньше двух — в группу ничего не шлётся, админам приходит ЛС.

## Установка

```powershell
pip install -r requirements.txt
copy .env.example .env                          # заполнить значения
alembic revision --autogenerate -m "init"       # сгенерировать миграцию по моделям
alembic upgrade head                            # применить — создать БД
python main.py
```

`alembic revision --autogenerate` сравнивает модели из `models.py` с текущей БД и
создаёт файл миграции в `alembic/versions/`; `alembic upgrade head` применяет его.
При первом запуске генерируется начальная схема. После изменения моделей повторите
обе команды.

## Развёртывание на Ubuntu (systemd)

Бот запускается как systemd-сервис с автоматическим перезапуском через 10 секунд
после остановки с ошибкой.

1. Создать отдельного системного пользователя `dbs` (бот не должен работать под root):

```bash
# создать пользователя с домашней директорией и оболочкой bash
sudo adduser --system --group --shell /bin/bash --home /home/dbs dbs

# войти под ним (открыть сессию от имени dbs)
sudo -u dbs -s
# ...выполняете команды из шага 2 от имени dbs...
# выйти из сессии обратно к своему пользователю:
exit
```

`--system` создаёт служебную учётку без пароля (под ней нельзя залогиниться по SSH,
только переключиться через `sudo`), `--group` заводит одноимённую группу.
`sudo -u dbs -s` открывает интерактивную оболочку от имени `dbs`; `exit` (или Ctrl-D)
закрывает её и возвращает к исходному пользователю.

2. Подготовка кода и окружения (выполнять под пользователем `dbs`):

```bash
# системные пакеты ставятся с правами root (один раз):
sudo apt update && sudo apt install -y python3 python3-venv git

# дальнейшее — от имени dbs (sudo -u dbs -s), в его директории:
git clone <repo-url> /home/dbs/dbs-staff-bot
cd /home/dbs/dbs-staff-bot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env                                  # заполнить значения
.venv/bin/alembic revision --autogenerate -m "init"   # сгенерировать миграцию
.venv/bin/alembic upgrade head                        # применить — создать БД
```

3. Создать unit-файл `/etc/systemd/system/dbs-staff-bot.service` (от root, через `sudo`):

```ini
[Unit]
Description=DBS Staff Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=dbs
WorkingDirectory=/home/dbs/dbs-staff-bot
EnvironmentFile=/home/dbs/dbs-staff-bot/.env
ExecStart=/home/dbs/dbs-staff-bot/.venv/bin/python main.py
Restart=on-failure
RestartSec=10
StartLimitIntervalSec=0

[Install]
WantedBy=multi-user.target
```

`Restart=on-failure` + `RestartSec=10` дают перезапуск через 10 секунд после
остановки с ошибкой. `StartLimitIntervalSec=0` отключает лимит на число
перезапусков, чтобы сервис поднимался бесконечно.

4. Запустить и включить автозапуск при загрузке системы:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now dbs-staff-bot
```

5. Полезные команды:

```bash
sudo systemctl status dbs-staff-bot      # статус
sudo journalctl -u dbs-staff-bot -f      # логи в реальном времени
sudo systemctl restart dbs-staff-bot     # перезапуск после обновления кода
```

После `git pull` обновите зависимости/миграции и перезапустите сервис (первые три
команды — от имени `dbs`, перезапуск сервиса — через `sudo`):

```bash
cd /home/dbs/dbs-staff-bot
.venv/bin/pip install -r requirements.txt
.venv/bin/alembic upgrade head            # применить новые миграции из репозитория
sudo systemctl restart dbs-staff-bot
```

## Настройка `.env`

| Переменная | Описание |
|---|---|
| `BOT_TOKEN` | токен от @BotFather |
| `GROUP_ID` | ID супергруппы (отрицательный) |
| `TOPIC_*_ID` | message_thread_id топиков (мусор / социалка / генералка) |
| `ADMIN_IDS` | ID админов через запятую |
| `TIMEZONE` | часовой пояс расписания |

ID группы и топиков удобно узнать командой `/chatinfo` — отправьте её в нужном топике,
бот ответит `chat.id` и `message_thread_id`.

## Команды бота (для админов)

- `/start` — меню: добавить / удалить / список сотрудников, статистика дежурств.
- `/chatinfo` — показать ID чата и топика.

## Где что править

- Тексты уведомлений — `templates.py`.
- Время отправок / дни недели — `SCHEDULE` в `config.py`.
- Логика подбора двоек — `duty.py`.

## Структура

`config.py` · `templates.py` · `database.py` · `models.py` · `tagging.py` ·
`duty.py` · `notifications.py` · `handlers.py` · `scheduler.py` · `main.py` ·
`alembic/` (миграции) · `smoke_duty.py` (локальный тест логики двоек).
