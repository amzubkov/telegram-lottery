# Telegram Lottery Bot

Бот для розыгрышей в Telegram. Админ создаёт розыгрыш, публикует сетку билетов в группу, участники разбирают билеты и оплачивают.

## Установка

```bash
git clone git@github.com:amzubkov/telegram-lottery.git
cd telegram-lottery
pip install -r requirements.txt
cp .env.example .env
```

## Настройка .env

```
BOT_TOKEN=токен_от_@BotFather
ADMIN_IDS=123456789
```

Свой Telegram ID можно узнать через @userinfobot. Несколько админов через запятую.

## Запуск

```bash
python3 bot.py
```

## Команды бота

| Команда | Кто | Что делает |
|---------|-----|------------|
| `/new` | админ | Создать розыгрыш (приз, кол-во билетов, цена, победители, реквизиты) |
| `/start` | админ | Опубликовать сетку билетов в чат |
| `/admin` | админ | Панель управления (отметить оплату, провести розыгрыш) |
| `/list` | админ | Список участников |
| `/my` | все | Мои билеты |

## Как работает

1. Админ: `/new` → заполняет параметры розыгрыша
2. Админ: `/start` в группе → появляется сетка билетов
3. Участники нажимают 🟢 кнопку → билет бронируется (⬜), участник видит реквизиты
4. Админ: `/admin` → нажимает на ⬜ билет → отмечает оплату (✅)
5. Админ: «Провести розыгрыш» → случайный выбор из оплаченных, победители получают уведомление

## Systemd (VPS)

```bash
sudo tee /etc/systemd/system/lottery-bot.service << 'EOF'
[Unit]
Description=Telegram Lottery Bot
After=network.target

[Service]
WorkingDirectory=/opt/telegram-lottery
ExecStart=/usr/bin/python3 /opt/telegram-lottery/bot.py
Restart=always
RestartSec=5
EnvironmentFile=/opt/telegram-lottery/.env

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now lottery-bot
```
