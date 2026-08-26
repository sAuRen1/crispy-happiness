import hmac
import os
import re
import secrets
from collections import defaultdict
from datetime import datetime, timedelta
from time import time

from dotenv import load_dotenv
from flask import Flask, abort, flash, redirect, render_template, request, session, url_for
from flask_sqlalchemy import SQLAlchemy
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
            abort(400, description='Форма устарела или отправлена не с этого сайта. Обновите страницу и попробуйте снова.')


# ===========================================================================
# ПРОСТОЙ RATE LIMITER (без сторонних библиотек)
# ---------------------------------------------------------------------------
# Считаем запросы по IP в скользящем окне. Защищает от:
#  - спама через форму записи (кто-то долбит POST / скриптом);
#  - перебора пароля админки (кто-то долбит POST /admin).
#
# Честное предупреждение (это важно понимать, а не просто скопировать код):
# состояние живёт в памяти ОДНОГО процесса. Если на проде запустишь
# `gunicorn -w 4` (4 воркера), у каждого будет своя память — лимит будет
# по факту в 4 раза мягче, чем указано. Для одного воркера или для сайта
# с некритичной нагрузкой это нормально. Если проект вырастет и нужно будет
# больше воркеров — лимитер нужно переносить на Redis (например,
# Flask-Limiter со storage_uri="redis://..."). Здесь это осознанное
# упрощение, а не то, что я забыл.
# ===========================================================================

_rate_buckets: dict[str, list[float]] = defaultdict(list)


def is_rate_limited(key: str, limit: int, window_seconds: int) -> bool:
    now = time()
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
        print("✅ База данных успешно подключена и таблицы созданы!")
    except Exception as e:
        print("❌ Ошибка подключения к базе:", e)


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
            flash('✅ Успешный вход в админку!', 'success')
            return redirect(url_for('admin'))

        flash('❌ Неверный логин или пароль', 'error')

    if session.get('admin_logged_in'):
        anketas = Anketa.query.order_by(Anketa.date.desc()).all()
        return render_template('admin.html', anketas=anketas)

    return render_template('admin_login.html')


@app.route('/admin/logout', methods=['POST'])
def admin_logout():
    session.pop('admin_logged_in', None)
    flash('Вы вышли из админки', 'success')
    return redirect(url_for('index'))


if __name__ == '__main__':
    app.run(debug=DEBUG, port=int(os.getenv('PORT', 5000)))
