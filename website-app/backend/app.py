import hmac
import logging
import os
import re
import secrets
import threading
from collections import defaultdict
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from time import time

from dotenv import load_dotenv
from flask import Flask, abort, flash, redirect, render_template, request, send_from_directory, session, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from werkzeug.security import check_password_hash

load_dotenv()

base_dir = os.path.dirname(os.path.abspath(__file__))

app = Flask(
    __name__,
    template_folder=os.path.join(base_dir, 'templates'),
    static_folder=os.path.join(base_dir, 'static')
)


# ===========================================================================
# КОНФИГУРАЦИЯ
# ---------------------------------------------------------------------------
# Все секреты (SECRET_KEY, пароль админки) приходят из переменных окружения,
# а не лежат текстом в этом файле. Причина простая: этот файл лежит в твоём
# git-репозитории, а репозиторий — публичный. Всё, что здесь захардкожено,
# фактически уже опубликовано в интернете.
#
# require_env() специально "падает" с понятной ошибкой, если переменная не
# задана, вместо того чтобы тихо подставить дефолт. Это называется
# fail-fast: лучше приложение не запустится и ты сразу увидишь проблему,
# чем оно "как-то" запустится в проде на дефолтном ключе.
# ===========================================================================

def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Не задана переменная окружения {name}. "
            f"Скопируй .env.example в .env и заполни значения "
            f"(см. website-app/README.md)."
        )
    return value


FLASK_ENV = os.getenv('FLASK_ENV', 'production')
DEBUG = FLASK_ENV == 'development'

app.config['SECRET_KEY'] = require_env('SECRET_KEY')

# Раньше здесь был захардкожен абсолютный Windows-путь (C:/сайт/...) —
# на любой другой машине (в том числе на боевом сервере) это привело бы
# к падению при старте. Теперь путь по умолчанию собирается относительно
# самого файла, а DATABASE_URL из .env позволяет переопределить его
# (например, на Postgres, если проект вырастет).
default_db_path = os.path.join(base_dir, 'instance', 'ankety.db')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv(
    'DATABASE_URL', f"sqlite:///{default_db_path}"
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Ограничение размера входящего запроса — простая защита от заливки
# огромных тел запроса на маленькую форму (грубый DoS).
app.config['MAX_CONTENT_LENGTH'] = 64 * 1024  # 64 КБ более чем достаточно

# Настройки cookie сессии (в неё пишется admin_logged_in).
app.config['SESSION_COOKIE_HTTPONLY'] = True      # JS не может прочитать cookie — снижает вред от XSS
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'      # базовая защита от CSRF на уровне браузера
app.config['SESSION_COOKIE_SECURE'] = not DEBUG    # cookie только по HTTPS в проде
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=8)

# Данные для входа в админку. Пароль хранится ХЭШЕМ (werkzeug pbkdf2:sha256),
# а не текстом — см. generate_admin_hash.py, чтобы сгенерировать новый.
app.config['ADMIN_USERNAME'] = os.getenv('ADMIN_USERNAME', 'admin')
app.config['ADMIN_PASSWORD_HASH'] = require_env('ADMIN_PASSWORD_HASH')


# ===========================================================================
# ЛОГИРОВАНИЕ
# ---------------------------------------------------------------------------
# Раньше важные события (подключение к БД, ошибки) просто печатались через
# print() — это работает, только пока ты сам смотришь в терминал рядом с
# запущенным процессом. На реальном хостинге такого терминала нет, а
# print() никак не помечен по важности (это заметка для себя или ошибка,
# которую надо разбирать ночью?) и никуда не сохраняется, если хостинг не
# перезапустит процесс с тем же терминалом.
#
# logging решает обе проблемы: у записи есть уровень (INFO — просто
# происходящее, WARNING — что-то подозрительное типа неверного пароля или
# сработавшего rate-limit, ERROR — реальная поломка), и можно направить её
# сразу в несколько мест.
#
# Мы пишем логи в stdout (консоль) — это стандартный подход для облачных
# хостингов (12-factor app): приложение просто печатает в консоль, а КУДА
# это дальше попадёт (файл, система мониторинга, раздел "Логи" у хостера)
# решает платформа, а не сам код. Amvera/Timeweb Cloud Apps по умолчанию
# ловят именно stdout процесса.
#
# Дополнительно пишем в файл рядом с базой — на случай, если тебе удобнее
# посмотреть историю локально, а не через интерфейс хостинга.
# RotatingFileHandler сам нарезает файл на куски по 1 МБ и хранит не
# больше 5 последних — иначе логи со временем заняли бы весь диск.
# ===========================================================================

def configure_logging(flask_app: Flask) -> None:
    log_level = logging.DEBUG if DEBUG else logging.INFO
    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S'
    )

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    flask_app.logger.addHandler(console_handler)
    flask_app.logger.setLevel(log_level)

    try:
        logs_dir = os.path.join(base_dir, 'instance')
        os.makedirs(logs_dir, exist_ok=True)
        file_handler = RotatingFileHandler(
            os.path.join(logs_dir, 'app.log'), maxBytes=1_000_000, backupCount=5, encoding='utf-8'
        )
        file_handler.setFormatter(formatter)
        flask_app.logger.addHandler(file_handler)
    except OSError:
        # Некоторые хостинги дают только временную/read-only файловую систему.
        # Логи в stdout всё равно продолжат работать — приложение из-за
        # этого падать не должно.
        flask_app.logger.warning('Не удалось создать файл логов — логируем только в консоль.')


configure_logging(app)

db = SQLAlchemy(app)


# ===========================================================================
# CSRF-ЗАЩИТА (без сторонних библиотек)
# ---------------------------------------------------------------------------
# Смысл атаки, от которой это защищает: злой сайт X может втихую отправить
# POST-запрос на твой /admin или на форму записи от имени залогиненного
# админа (его браузер сам приложит cookie сессии). CSRF-токен — это
# случайное значение, которое сервер кладёт в сессию и в скрытое поле
# формы. Чужой сайт X не может прочитать твою сессию, поэтому не может
# подставить правильный токен — и POST отклоняется.
#
# Можно было подключить Flask-WTF (готовая библиотека для того же самого),
# но она тянет за собой WTForms целиком ради одной функции. Для двух форм
# в проекте хватает 15 строк ниже — и ты понимаешь, что именно происходит.
# ===========================================================================

def generate_csrf_token() -> str:
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_urlsafe(32)
    return session['csrf_token']


app.jinja_env.globals['csrf_token'] = generate_csrf_token


@app.before_request
def csrf_protect():
    if request.method == 'POST':
        token_in_session = session.get('csrf_token')
        token_submitted = request.form.get('csrf_token')
        if not token_in_session or not token_submitted or not hmac.compare_digest(
            token_in_session, token_submitted
        ):
            app.logger.warning(
                'CSRF-проверка не пройдена: IP=%s, путь=%s', request.remote_addr, request.path
            )
            abort(400, description='Форма устарела или отправлена не с этого сайта. Обновите страницу и попробуйте снова.')


# ===========================================================================
# ПРОСТОЙ RATE LIMITER (без сторонних библиотек)
# ---------------------------------------------------------------------------
# Считаем запросы по IP в скользящем окне. Защищает от:
#  - спама через форму записи (кто-то долбит POST / скриптом);
#  - перебора пароля админки (кто-то долбит POST /admin).
#
# Честное предупреждение про масштабирование (важно понимать, а не просто
# скопировать код): состояние живёт в памяти ОДНОГО процесса. Если на
# проде запустишь несколько gunicorn-ВОРКЕРОВ (`-w 4`, отдельные процессы),
# у каждого будет своя копия этого словаря — лимит станет мягче в 4 раза,
# т.к. атакующего IP посчитают отдельно в каждом процессе.
#
# Поэтому для этого проекта в README рекомендован ОДИН процесс с
# несколькими ПОТОКАМИ (`--workers 1 --threads N`, gthread-воркер), а не
# несколько процессов: потоки одного процесса делят одну и ту же память,
# значит и этот словарь общий, и лимит считается честно, при этом сервер
# всё равно параллельно обрабатывает много запросов. Если сайт вырастет
# настолько, что понадобится несколько процессов/машин — тогда лимитер
# действительно нужно переносить на Redis (Flask-Limiter со
# storage_uri="redis://..."). Пока это осознанное упрощение под масштаб
# сайта-визитки, а не то, что забыли.
#
# Lock нужен именно из-за потоков: без него два потока могут одновременно
# читать и изменять список bucket для одного и того же IP — например, оба
# увидят len(bucket) == 4 и оба решат "лимит не превышен", пропустив
# 6-й запрос вместо блокировки. Race condition, которая никак не проявится
# на одном потоке (как было раньше), но реальна под конкурентной нагрузкой.
# ===========================================================================

_rate_buckets: dict[str, list[float]] = defaultdict(list)
_rate_lock = threading.Lock()


def is_rate_limited(key: str, limit: int, window_seconds: int) -> bool:
    now = time()
    with _rate_lock:
        bucket = _rate_buckets[key]
        while bucket and bucket[0] <= now - window_seconds:
            bucket.pop(0)
        if len(bucket) >= limit:
            return True
        bucket.append(now)
        return False


# ===========================================================================
# HTTP SECURITY HEADERS
# ---------------------------------------------------------------------------
# Заголовки, которые ничего не стоят по производительности, но закрывают
# целые классы атак:
#  - X-Content-Type-Options — браузер не будет "угадывать" тип файла
#    (защита от подсовывания скрипта под видом картинки);
#  - X-Frame-Options — сайт нельзя встроить в <iframe> на чужой странице
#    (защита от clickjacking);
#  - Content-Security-Policy — явно говорим браузеру, откуда можно
#    грузить скрипты/стили. script-src 'self' means "только наши файлы" —
#    даже если атакующий как-то вставит <script>, браузер его не выполнит.
#  - Strict-Transport-Security — включаем только вне debug, потому что
#    он говорит браузеру "всегда ходи по HTTPS", а локально у тебя HTTP.
# ===========================================================================

@app.after_request
def set_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "img-src 'self' data:; "
        "style-src 'self' 'unsafe-inline'; "  # 'unsafe-inline' нужен из-за style="..." во flash-сообщении
        "script-src 'self'"
    )
    if not DEBUG:
        response.headers['Strict-Transport-Security'] = 'max-age=63072000; includeSubDomains'
    return response


class Anketa(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100))
    phone = db.Column(db.String(30))
    message = db.Column(db.Text)
    date = db.Column(db.DateTime, default=datetime.utcnow)


# Создаём таблицы. Модель должна быть объявлена ВЫШЕ этого блока —
# иначе create_all() не увидит её и не создаст таблицу (в оригинальном
# коде было ровно так: класс Anketa шёл ПОСЛЕ create_all(), и это
# случайно работало только потому, что таблица уже существовала на диске
# с прошлого запуска; на чистой БД это привело бы к ошибке "no such
# table: anketa" при первой же записи).
with app.app_context():
    try:
        db.create_all()

        # SQLite: включаем WAL (Write-Ahead Logging).
        # По умолчанию SQLite при записи блокирует ВСЮ базу целиком — пока
        # кто-то отправляет форму записи (INSERT), любое чтение (например,
        # ты открыл админку в этот момент) ждёт своей очереди. При росте
        # нагрузки это и есть узкое место, а не CPU и не Python.
        # В режиме WAL писатель пишет в отдельный журнал, а читатели
        # продолжают читать текущую версию базы — читатели и писатель
        # не блокируют друг друга. Ограничение: WAL не работает по сети
        # (не годится для БД на сетевом диске), но для sqlite-файла на
        # локальном диске сервера — стандартная и безопасная настройка.
        # Это persistent-настройка: она сохраняется в самом файле базы,
        # поэтому её достаточно применить один раз при старте.
        if app.config['SQLALCHEMY_DATABASE_URI'].startswith('sqlite'):
            db.session.execute(text('PRAGMA journal_mode=WAL'))

        is_sqlite = app.config['SQLALCHEMY_DATABASE_URI'].startswith('sqlite')
        app.logger.info('База данных подключена, таблицы созданы (WAL включён: %s)', is_sqlite)
    except Exception:
        app.logger.exception('Ошибка подключения к базе данных')


EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        # Honeypot: невидимое обычным людям поле (спрятано в CSS).
        # Боты, которые автоматически заполняют все поля формы, заполнят
        # и его. Живой человек его никогда не увидит и не тронет.
        if request.form.get('company'):
            flash('✅ Спасибо! Ваша заявка отправлена. Мы скоро свяжемся с вами.', 'success')
            return redirect(url_for('index'))

        if is_rate_limited(f"booking:{request.remote_addr}", limit=5, window_seconds=60):
            app.logger.warning('Rate limit: слишком много заявок с IP=%s', request.remote_addr)
            flash('❌ Слишком много заявок подряд. Попробуйте через минуту.', 'error')
            return redirect(url_for('index'))

        name = request.form.get('name', '').strip()[:100]
        email = request.form.get('email', '').strip()[:100]
        phone = request.form.get('phone', '').strip()[:30]
        message = request.form.get('message', '').strip()[:2000]

        if not name:
            flash('❌ Пожалуйста, укажите ваше имя.', 'error')
            return redirect(url_for('index'))

        if email and not EMAIL_RE.match(email):
            flash('❌ Проверьте, пожалуйста, email — он выглядит некорректно.', 'error')
            return redirect(url_for('index'))

        new_anketa = Anketa(name=name, email=email, phone=phone, message=message)
        db.session.add(new_anketa)
        db.session.commit()
        flash('✅ Спасибо! Ваша заявка отправлена. Мы скоро свяжемся с вами.', 'success')
        return redirect(url_for('index'))

    return render_template('index.html')


# ====================== АДМИНКА ======================
@app.route('/admin', methods=['GET', 'POST'])
def admin():
    if request.method == 'POST':
        if is_rate_limited(f"admin-login:{request.remote_addr}", limit=5, window_seconds=300):
            app.logger.warning(
                'Rate limit: слишком много попыток входа в админку с IP=%s', request.remote_addr
            )
            flash('❌ Слишком много попыток входа. Попробуйте через 5 минут.', 'error')
            return render_template('admin_login.html')

        username = request.form.get('username', '')
        password = request.form.get('password', '')

        valid_username = hmac.compare_digest(username, app.config['ADMIN_USERNAME'])
        valid_password = check_password_hash(app.config['ADMIN_PASSWORD_HASH'], password)

        if valid_username and valid_password:
            session.clear()
            session['admin_logged_in'] = True
            session.permanent = True
            app.logger.info('Успешный вход в админку с IP=%s', request.remote_addr)
            flash('✅ Успешный вход в админку!', 'success')
            return redirect(url_for('admin'))

        # Логируем IP и введённый логин (НЕ пароль — пароль в логах не должен
        # оказаться никогда, даже неверный) — пригодится, если понадобится
        # понять, кто и как часто пытается подобрать доступ в админку.
        app.logger.warning(
            'Неудачная попытка входа в админку: логин=%r, IP=%s', username, request.remote_addr
        )
        flash('❌ Неверный логин или пароль', 'error')

    if session.get('admin_logged_in'):
        anketas = Anketa.query.order_by(Anketa.date.desc()).all()
        return render_template('admin.html', anketas=anketas)

    return render_template('admin_login.html')


@app.route('/admin/logout', methods=['POST'])
def admin_logout():
    session.pop('admin_logged_in', None)
    app.logger.info('Выход из админки, IP=%s', request.remote_addr)
    flash('Вы вышли из админки', 'success')
    return redirect(url_for('index'))


# ================= ЮРИДИЧЕСКИЕ СТРАНИЦЫ =================
# Форма записи собирает персональные данные (имя, email, телефон) и прямо
# ссылается на эти страницы как на согласие по 152-ФЗ — значит, ссылки
# не могут вести в никуда. Текст в шаблонах — типовой черновик, а не
# готовый юридический документ: перед реальным запуском Макс должен
# вписать туда свои настоящие реквизиты (см. TODO в самих шаблонах).
@app.route('/privacy')
def privacy():
    return render_template('privacy.html')


@app.route('/terms')
def terms():
    return render_template('terms.html')


# robots.txt лежит в static/, но поисковики и краулеры всегда стучатся
# именно в /robots.txt в корне сайта, а не в /static/robots.txt — этот
# роут отдаёт тот же файл по правильному адресу. Disallow: /admin — чтобы
# страница входа в админку не попала в индекс поисковика.
@app.route('/robots.txt')
def robots_txt():
    return send_from_directory(app.static_folder, 'robots.txt')


# ===========================================================================
# HEALTH-CHECK
# ---------------------------------------------------------------------------
# Отдельный лёгкий эндпоинт, который отвечает на вопрос "сайт реально
# работает?" — не просто "процесс запущен" (это может ответить и намертво
# зависшее приложение), а "может ли он сходить в базу данных". Хостинги
# и балансировщики умеют периодически дёргать такой адрес и сами
# перезапускать процесс, если он перестал отвечать 200 OK — это и есть
# базовое автовосстановление без участия человека.
# Специально не рендерим HTML-шаблон — health-check дёргается часто и
# автоматически, ему не нужен красивый дизайн, только быстрый ответ.
# ===========================================================================

@app.route('/healthz')
def healthz():
    try:
        db.session.execute(text('SELECT 1'))
        return {'status': 'ok'}, 200
    except Exception:
        app.logger.exception('Health-check: база данных недоступна')
        return {'status': 'error', 'detail': 'database unavailable'}, 503


# ===========================================================================
# СТРАНИЦЫ ОШИБОК
# ---------------------------------------------------------------------------
# Без этих обработчиков Flask/Werkzeug показывает свою дефолтную белую
# страницу с текстом ошибки — она выглядит как техническая заглушка и
# ничего не даёт посетителю сделать, кроме как закрыть вкладку. Плюс
# 500-страница Flask по умолчанию в проде показывает голый "Internal
# Server Error" без единой зацепки, что происходит — так и должно быть
# (детали ошибки не должны утекать наружу), но человек должен увидеть
# хотя бы кнопку "на главную", а не тупик.
# ===========================================================================

@app.errorhandler(400)
def bad_request(e):
    message = getattr(e, 'description', None) or (
        'Запрос не может быть обработан. Обновите страницу и попробуйте снова.'
    )
    return render_template('error.html', code=400, title='Некорректный запрос', message=message), 400


@app.errorhandler(404)
def not_found(e):
    return render_template(
        'error.html', code=404, title='Страница не найдена',
        message='Такой страницы не существует — возможно, ссылка устарела или в адресе опечатка.'
    ), 404


@app.errorhandler(413)
def too_large(e):
    return render_template(
        'error.html', code=413, title='Слишком большой запрос',
        message='Отправленные данные превышают допустимый размер.'
    ), 413


@app.errorhandler(500)
def internal_error(e):
    # Откатываем сессию БД: если ошибка произошла посреди работы с базой,
    # сессия может остаться в "грязном" состоянии и сломать СЛЕДУЮЩИЙ
    # запрос, который её переиспользует, даже если та ошибка была разовой.
    db.session.rollback()
    app.logger.exception('Необработанная ошибка сервера')
    return render_template(
        'error.html', code=500, title='Что-то пошло не так',
        message='На сервере произошла ошибка. Мы уже разбираемся — попробуйте, пожалуйста, чуть позже.'
    ), 500


if __name__ == '__main__':
    # threaded=True — только для локального запуска через `python app.py`.
    # На проде эта строка вообще не выполняется: там сайт запускает
    # gunicorn (см. README, раздел "Деплой"), а не этот файл напрямую.
    app.run(debug=DEBUG, port=int(os.getenv('PORT', 5000)), threaded=True)
