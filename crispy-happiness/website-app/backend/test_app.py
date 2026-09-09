"""
Автоматические тесты для backend/app.py — на стандартном unittest
(pytest НЕ нужен, unittest есть в Python "из коробки").

Что тут проверяется и зачем — по каждому тесту отдельно ниже, но общая
идея: не проверять руками в браузере одно и то же после каждого
изменения кода, а запустить одну команду и за секунды узнать, не
сломалось ли что-то из уже работавшего (это называется регрессионное
тестирование).

Тесты используют СВОЮ временную базу данных (создаётся и удаляется
автоматически) — реальную ankety.db они не трогают.

Запуск (из папки website-app/backend):
    python3 -m unittest test_app.py -v
"""
import os
import re
import tempfile
import unittest

# --- Тестовое окружение -----------------------------------------------
# app.py требует SECRET_KEY и ADMIN_PASSWORD_HASH СРАЗУ при импорте
# (см. require_env() в app.py — осознанный fail-fast). Поэтому выставляем
# переменные окружения ДО того, как первый раз импортируем app.
#
# Используем СВОИ значения, а не то, что лежит в реальном .env — тесты не
# должны зависеть от содержимого .env на конкретной машине, иначе они
# будут то проходить, то падать в зависимости от того, что там записано.
_TEST_DB_FD, TEST_DB_PATH = tempfile.mkstemp(suffix='.db')
os.close(_TEST_DB_FD)

TEST_ADMIN_PASSWORD = 'test-admin-password-123'
# Хэш ИМЕННО этого пароля, сгенерированный werkzeug.security.generate_password_hash.
# К реальному паролю админки не имеет никакого отношения.
TEST_ADMIN_PASSWORD_HASH = (
    'scrypt:32768:8:1$gOiUpjQu6eJ3F7TX$82d1e2fca41aefafee12ff3205d16d9dfc2f0c5'
    'beda67789d1d8e758261fccfa3fd266870ca756bdef025cc33fe8f64f67fd64b0335d002a'
    '31a1c461584fcecf'
)

os.environ['FLASK_ENV'] = 'development'
os.environ['SECRET_KEY'] = 'test-secret-key-only-for-unittest'
os.environ['ADMIN_USERNAME'] = 'admin'
os.environ['ADMIN_PASSWORD_HASH'] = TEST_ADMIN_PASSWORD_HASH
os.environ['DATABASE_URL'] = f'sqlite:///{TEST_DB_PATH}'

import app as app_module  # noqa: E402 — импорт после настройки окружения, осознанно


class PyramidkaTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.flask_app = app_module.app
        cls.flask_app.config['TESTING'] = True

    def setUp(self):
        self.client = self.flask_app.test_client()
        # Сбрасываем rate-limiter между тестами: без этого тесты бы
        # мешали друг другу, потому что все идут с одного тестового IP
        # (127.0.0.1) и делят один и тот же словарь в памяти процесса.
        app_module._rate_buckets.clear()
        # Чистим таблицу анкет — тесты не должны зависеть от порядка
        # выполнения друг друга или от результатов предыдущего теста.
        with self.flask_app.app_context():
            app_module.Anketa.query.delete()
            app_module.db.session.commit()

    def _get_csrf_token(self, path='/'):
        resp = self.client.get(path)
        html = resp.get_data(as_text=True)
        match = re.search(r'name="csrf_token" value="([^"]+)"', html)
        self.assertIsNotNone(match, f'CSRF-токен не найден на странице {path}')
        return match.group(1)

    # ------------------------- главная страница -------------------------

    def test_index_get_ok(self):
        resp = self.client.get('/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('csrf_token', resp.get_data(as_text=True))

    def test_booking_without_csrf_token_rejected(self):
        # Без CSRF-токена запрос должен быть отклонён — иначе любой чужой
        # сайт мог бы слать заявки от имени твоих посетителей их браузером.
        resp = self.client.post('/', data={'name': 'Тест'})
        self.assertEqual(resp.status_code, 400)

    def test_booking_valid_data_saves_to_db(self):
        token = self._get_csrf_token()
        resp = self.client.post('/', data={
            'csrf_token': token,
            'name': 'Иван',
            'email': 'ivan@example.com',
            'phone': '+7 900 000-00-00',
            'message': 'Хочу записаться на пробное занятие',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        with self.flask_app.app_context():
            anketas = app_module.Anketa.query.all()
        self.assertEqual(len(anketas), 1)
        self.assertEqual(anketas[0].name, 'Иван')
        self.assertEqual(anketas[0].email, 'ivan@example.com')

    def test_booking_without_name_rejected(self):
        token = self._get_csrf_token()
        resp = self.client.post('/', data={
            'csrf_token': token, 'name': '   ',
        }, follow_redirects=True)
        self.assertIn('укажите ваше имя', resp.get_data(as_text=True))
        with self.flask_app.app_context():
            self.assertEqual(len(app_module.Anketa.query.all()), 0)

    def test_booking_invalid_email_rejected(self):
        token = self._get_csrf_token()
        resp = self.client.post('/', data={
            'csrf_token': token, 'name': 'Иван', 'email': 'это-не-email',
        }, follow_redirects=True)
        self.assertIn('email', resp.get_data(as_text=True))
        with self.flask_app.app_context():
            self.assertEqual(len(app_module.Anketa.query.all()), 0)

    def test_booking_honeypot_field_silently_ignored(self):
        # Живой человек никогда не видит и не заполняет поле "company"
        # (оно спрятано в CSS). Если оно заполнено — это бот. Заявку не
        # сохраняем, но и не даём боту понять, что его вычислили (он видит
        # то же "спасибо", что и обычный пользователь).
        token = self._get_csrf_token()
        resp = self.client.post('/', data={
            'csrf_token': token, 'name': 'Бот-спамер', 'company': 'заполнено ботом',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        with self.flask_app.app_context():
            self.assertEqual(len(app_module.Anketa.query.all()), 0)

    def test_booking_rate_limit_kicks_in(self):
        # В коде лимит: 5 заявок за 60 секунд с одного IP.
        for _ in range(5):
            token = self._get_csrf_token()
            resp = self.client.post('/', data={
                'csrf_token': token, 'name': 'Иван',
            }, follow_redirects=True)
            self.assertEqual(resp.status_code, 200)

        token = self._get_csrf_token()
        resp = self.client.post('/', data={
            'csrf_token': token, 'name': 'Иван',
        }, follow_redirects=True)
        self.assertIn('Слишком много заявок', resp.get_data(as_text=True))
        with self.flask_app.app_context():
            # Ровно 5 заявок должны были сохраниться, 6-я — заблокирована.
            self.assertEqual(len(app_module.Anketa.query.all()), 5)

    # ------------------------------ админка ------------------------------

    def test_admin_page_shows_login_form_when_not_logged_in(self):
        resp = self.client.get('/admin')
        self.assertIn('Вход в админку', resp.get_data(as_text=True))

    def test_admin_wrong_password_rejected(self):
        token = self._get_csrf_token('/admin')
        resp = self.client.post('/admin', data={
            'csrf_token': token, 'username': 'admin', 'password': 'совершенно-неверный-пароль',
        }, follow_redirects=True)
        self.assertIn('Неверный логин или пароль', resp.get_data(as_text=True))

    def test_admin_correct_password_logs_in_and_shows_anketas(self):
        token = self._get_csrf_token('/admin')
        resp = self.client.post('/admin', data={
            'csrf_token': token, 'username': 'admin', 'password': TEST_ADMIN_PASSWORD,
        }, follow_redirects=True)
        self.assertIn('Успешный вход', resp.get_data(as_text=True))
        # Без анкет должен показываться empty-state, а не пустая таблица.
        self.assertIn('Пока нет ни одной заявки', resp.get_data(as_text=True))

    def test_admin_login_rate_limit_kicks_in(self):
        # В коде лимит: 5 попыток входа за 300 секунд с одного IP.
        for _ in range(5):
            token = self._get_csrf_token('/admin')
            self.client.post('/admin', data={
                'csrf_token': token, 'username': 'admin', 'password': 'неверный',
            })

        token = self._get_csrf_token('/admin')
        resp = self.client.post('/admin', data={
            'csrf_token': token, 'username': 'admin', 'password': 'неверный',
        }, follow_redirects=True)
        self.assertIn('Слишком много попыток входа', resp.get_data(as_text=True))

    def test_admin_logout_clears_session(self):
        token = self._get_csrf_token('/admin')
        self.client.post('/admin', data={
            'csrf_token': token, 'username': 'admin', 'password': TEST_ADMIN_PASSWORD,
        })
        token = self._get_csrf_token('/admin')  # уже залогинен, но токен всё равно нужен для POST
        self.client.post('/admin/logout', data={'csrf_token': token}, follow_redirects=True)
        resp = self.client.get('/admin')
        self.assertIn('Вход в админку', resp.get_data(as_text=True))

    # --------------------------- инфраструктура ---------------------------

    def test_healthz_returns_ok(self):
        resp = self.client.get('/healthz')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), {'status': 'ok'})

    def test_unknown_page_returns_custom_404(self):
        resp = self.client.get('/такой-страницы-точно-не-существует')
        self.assertEqual(resp.status_code, 404)
        self.assertIn('Страница не найдена', resp.get_data(as_text=True))

    def test_privacy_and_terms_pages_load(self):
        self.assertEqual(self.client.get('/privacy').status_code, 200)
        self.assertEqual(self.client.get('/terms').status_code, 200)

    def test_security_headers_present(self):
        resp = self.client.get('/')
        self.assertEqual(resp.headers.get('X-Frame-Options'), 'DENY')
        self.assertEqual(resp.headers.get('X-Content-Type-Options'), 'nosniff')
        self.assertIn('Content-Security-Policy', resp.headers)

    @classmethod
    def tearDownClass(cls):
        try:
            os.remove(TEST_DB_PATH)
        except OSError:
            pass


if __name__ == '__main__':
    unittest.main()
