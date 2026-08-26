# Центр «Пирамидка» — сайт и админка

Flask-приложение: публичная страница с формой записи + закрытая админка
со списком заявок.

## Быстрый старт (локально)

```bash
cd website-app/backend
python -m venv venv && source venv/bin/activate     # можно и без venv
pip install -r requirements.txt

cp .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"   # вставить в .env как SECRET_KEY
python generate_admin_hash.py                                # вставить вывод в .env как ADMIN_PASSWORD_HASH

python app.py
# открыть http://127.0.0.1:5000, админка — http://127.0.0.1:5000/admin
```

`FLASK_ENV=development` в `.env` включает debug-режим Flask. Больше НИГДЕ,
кроме своей машины, этот режим не включай — см. ниже, почему.

---

## ⚠️ Если у тебя уже был закоммичен .env или *.db — сделай это в первую очередь

В более старой версии этого репозитория (публичного на GitHub) были
закоммичены: пароль администратора открытым текстом в `app.py`, `.env`
с ключом и `instance/ankety.db` — реальная заявка (имя, email, телефон)
человека, который тестировал форму. Раз репозиторий публичный, эти данные
уже были технически доступны кому угодно.

Что сделать прямо сейчас, по порядку:

1. Обновить `app.py` и шаблоны на версию из этой поставки (пароль теперь
   не хардкодится, а приходит из `.env` в виде хэша).
2. Перестать отслеживать секреты в git:
   ```bash
   git rm --cached website-app/backend/.env
   git rm --cached instance/ankety.db website-app/backend/instance/ankety.db
   git add .gitignore
   git commit -m "Убрать секреты и базу данных из git"
   git push
   ```
3. Считать старый пароль администратора и старый `SECRET_KEY` **скомпрометированными** —
   сгенерировать новые (команды выше) и никогда не использовать прежние значения.
4. (Опционально, но по-хорошему нужно) Эти файлы всё ещё лежат в старых
   коммитах истории GitHub — шаг 2 не удаляет их оттуда, а только
   останавливает отслеживание вперёд. Полностью вычистить историю можно так:
   ```bash
   pip install git-filter-repo
   git filter-repo --path website-app/backend/.env \
                    --path instance/ankety.db \
                    --path website-app/backend/instance/ankety.db \
                    --invert-paths
   git push --force
   ```
   Это переписывает историю и требует force-push — операция необратимая.
   Если репозиторий соло-учебный и его никто больше не клонировал — обычно
   безопасно. Если сомневаешься — можно и не делать: данные уже все равно
   когда-то были в открытом доступе, и как минимум пароль/ключ уже заменены
   на новые в шаге 3, это главное.

---

## Переменные окружения (`.env`)

| Переменная | Обязательна | Описание |
|---|---|---|
| `SECRET_KEY` | да | Подписывает cookie сессии. `python -c "import secrets; print(secrets.token_hex(32))"` |
| `ADMIN_USERNAME` | нет (по умолчанию `admin`) | Логин администратора |
| `ADMIN_PASSWORD_HASH` | да | Хэш пароля, не сам пароль. Генерируется `generate_admin_hash.py` |
| `FLASK_ENV` | нет (по умолчанию `production`) | `development` — только для локальной разработки |
| `DATABASE_URL` | нет | По умолчанию `instance/ankety.db` рядом с `app.py` |
| `PORT` | нет (по умолчанию `5000`) | Порт для `python app.py` (на проде порт задаёт gunicorn) |

`.env` в `.gitignore` — никогда не коммить этот файл.

---

## Что уже защищено и почему

- **CSRF-токен** на форме записи и на входе в админку — без него левый сайт
  не сможет отправить запрос от имени залогиненного администратора.
- **Rate limiting** — не больше 5 заявок в минуту и не больше 5 попыток входа
  в админку за 5 минут с одного IP. Хранится в памяти процесса — см.
  ограничение ниже.
- **Honeypot** на форме записи — невидимое людям поле, которое ловит простых
  ботов-заполнителей форм.
- **Пароль администратора — хэш** (`werkzeug` pbkdf2/scrypt), сравнение через
  `check_password_hash`, а не текстовое сравнение.
- **Security headers**: `X-Content-Type-Options`, `X-Frame-Options`,
  `Content-Security-Policy` (запрещает выполнение inline `<script>`),
  `Referrer-Policy`, `Strict-Transport-Security` (вне debug-режима).
- **Cookie сессии**: `HttpOnly` (недоступна из JS), `SameSite=Lax`, `Secure`
  вне debug-режима (передаётся только по HTTPS).
- **SQL-инъекции**: весь доступ к БД идёт через ORM (`SQLAlchemy`/параметризованные
  запросы), нигде нет ручной сборки SQL строками — это уже защищено by design.
- **XSS в админке**: Jinja2 по умолчанию экранирует всё, что выводится через
  `{{ ... }}` — значения из формы (`anketa.name`, `anketa.message` и т. д.)
  не могут быть исполнены как HTML/JS, если только явно не поставить фильтр
  `|safe` (в шаблонах его нигде нет).
- **`MAX_CONTENT_LENGTH`** — сервер отклоняет запросы тяжелее 64 КБ (простая
  форма не должна весить больше).

## Известные ограничения — честно, а не "и так сойдёт"

- **Rate limiter в памяти одного процесса.** Если на проде запустишь
  `gunicorn -w 4` (4 воркера), у каждого своя память — лимит станет мягче
  в 4 раза. Для одного воркера или сайта с некритичной нагрузкой — нормально.
  Если понадобится больше воркеров — лимитер нужно переносить на Redis
  (`Flask-Limiter` со `storage_uri="redis://..."`).
- **`SESSION_COOKIE_SECURE=True` вне debug** означает, что сайт **обязан**
  открываться по HTTPS в проде — иначе браузер не отправит cookie сессии
  обратно, и вход в админку с формой перестанут работать вообще (не станут
  менее безопасными, а именно перестанут работать). Это осознанно —
  так и должно быть, но не забудь настроить сертификат перед тем, как
  включать `FLASK_ENV=production`.
- CSP разрешает `'unsafe-inline'` для стилей (из-за инлайн `style=""` во
  flash-сообщении в `index.html`) — если захочешь ужесточить дальше, вынеси
  этот стиль в CSS-класс и убери `'unsafe-inline'`.

---

## Деплой на сервер с доменом

Коротко, шаги для обычного VPS (Ubuntu) без привязки к конкретному хостингу:

1. **Код и окружение на сервере**
   ```bash
   git clone <твой репозиторий>
   cd .../website-app/backend
   python3 -m venv venv && source venv/bin/activate
   pip install -r requirements.txt
   cp .env.example .env   # заполнить реальными значениями, FLASK_ENV=production
   chmod 600 .env          # только владелец может читать файл с секретами
   ```

2. **Запуск через gunicorn** (не через `python app.py` — это dev-сервер
   Flask, в логах сам предупреждает, что не для продакшена):
   ```bash
   gunicorn -w 2 -b 127.0.0.1:8000 app:app
   ```
   `-w 2` — два воркера-процесса (см. ограничение rate limiter'а выше).

3. **systemd**, чтобы приложение поднималось само после перезагрузки сервера
   (`/etc/systemd/system/piramidka.service`):
   ```ini
   [Unit]
   Description=Piramidka Flask app
   After=network.target

   [Service]
   User=www-data
   WorkingDirectory=/path/to/website-app/backend
   EnvironmentFile=/path/to/website-app/backend/.env
   ExecStart=/path/to/venv/bin/gunicorn -w 2 -b 127.0.0.1:8000 app:app
   Restart=always

   [Install]
   WantedBy=multi-user.target
   ```
   ```bash
   sudo systemctl enable --now piramidka
   ```

4. **Nginx как reverse proxy** перед gunicorn (Nginx отдаёт HTTPS, gunicorn
   слушает только `127.0.0.1`, наружу не торчит):
   ```nginx
   server {
       listen 80;
       server_name твой-домен.ru;

       location / {
           proxy_pass http://127.0.0.1:8000;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto $scheme;
       }
   }
   ```

5. **HTTPS через Let's Encrypt (certbot)** — обязателен, см. ограничение
   про `SESSION_COOKIE_SECURE` выше:
   ```bash
   sudo apt install certbot python3-certbot-nginx
   sudo certbot --nginx -d твой-домен.ru
   ```
   Certbot сам допишет в Nginx-конфиг `listen 443 ssl` и настроит редирект
   с 80 на 443.

6. **Файрвол** — открыть только 80/443 и SSH (желательно по ключу, не паролю):
   ```bash
   sudo ufw allow OpenSSH
   sudo ufw allow 'Nginx Full'
   sudo ufw enable
   ```

7. **Бэкапы `instance/ankety.db`** — это единственное место, где хранятся
   реальные заявки людей. Простой вариант — ежедневный `cron`, копирующий
   файл в другое место (или в облако):
   ```bash
   0 3 * * * cp /path/to/instance/ankety.db /path/to/backups/ankety-$(date +\%F).db
   ```

---

## Структура

```
website-app/backend/
├── app.py                    # вся логика: роуты, конфиг, CSRF, rate limit, security headers
├── generate_admin_hash.py    # утилита: сгенерировать ADMIN_PASSWORD_HASH
├── .env.example               # шаблон .env без реальных секретов
├── requirements.txt
├── static/
│   ├── css/base.css
│   ├── js/app.js
│   └── icons/, images/
├── templates/
│   ├── index.html             # публичная страница + форма записи
│   ├── admin.html             # список заявок (только для залогиненных)
│   └── admin_login.html
└── instance/ankety.db         # SQLite база — НЕ в git (см. .gitignore)
```
