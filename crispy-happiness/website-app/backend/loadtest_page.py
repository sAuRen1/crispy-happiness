"""
Простой нагрузочный тест для сайта "Пирамидка" — без сторонних библиотек,
только стандартная библиотека Python (запустится где угодно, даже без pip).

Зачем: не верить на слово "сайт выдержит 100 человек", а реально проверить.
Скрипт симулирует N человек, каждый из которых одновременно открывает
главную страницу (HTML + CSS + JS + все картинки — именно так браузер
и грузит страницу), и показывает: сколько запросов прошло успешно, сколько
упало с ошибкой, и сколько времени люди реально ждали (p50/p95 — типичная
и почти-худшая задержка, а не только средняя, которую легко приукрасить).

Использование:
    python3 loadtest_page.py <URL> <сколько_человек> <параллельных_потоков>

Примеры:
    # Локально, пока сайт крутится через `python app.py`:
    python3 loadtest_page.py http://127.0.0.1:5000 50 100

    # После деплоя — САМАЯ ВАЖНАЯ проверка, локальный тест не показатель:
    python3 loadtest_page.py https://твой-домен.ru 100 150

Важно: гоняй тест с ДРУГОГО компьютера, не с самого сервера — иначе
меряешь скорость сервера самого к себе, без реальной сети, и результат
будет обманчиво быстрым.
"""
import sys
import time
import concurrent.futures as cf
import urllib.request

# Все запросы, которые браузер реально делает при открытии главной страницы.
# Если поменяешь картинки на сайте — можно поправить и здесь, но для теста
# нагрузки не критично: важно общее количество и размер запросов, а не
# конкретные файлы.
ASSETS = [
    "/",
    "/static/css/base.css",
    "/static/js/app.js",
    "/static/icons/1.png",
    "/static/icons/2.png",
    "/static/icons/3.png",
    "/static/icons/4.png",
    "/static/icons/5.png",
    "/static/icons/6.png",
    "/static/images/photo_2026-08-26_20-20-05.jpg",
    "/static/images/onas1.jfif",
]


def hit(base: str, path: str) -> tuple[object, float]:
    t0 = time.time()
    try:
        with urllib.request.urlopen(base + path, timeout=20) as r:
            code = r.status
            r.read()  # реально скачиваем тело, а не только заголовки
    except Exception as e:
        code = f"ERR:{e}"
    return code, time.time() - t0


def run(base: str, users: int, workers: int) -> None:
    requests_to_send = [asset for _ in range(users) for asset in ASSETS]

    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(lambda path: hit(base, path), requests_to_send))
    total = time.time() - t0

    codes: dict[object, int] = {}
    times: list[float] = []
    for code, dt in results:
        codes[code] = codes.get(code, 0) + 1
        times.append(dt)
    times.sort()
    n = len(times)

    print(f"Адрес: {base}")
    print(f"Симулировано человек: {users}  (запросов всего: {n}, параллельно: {workers})")
    print(f"Общее время теста: {total:.2f} сек   пропускная способность: {n / total:.1f} запросов/сек")
    print(f"Коды ответов: {codes}")
    print(
        f"Задержка — типичная (p50): {times[n // 2]:.3f} сек, "
        f"почти-худшая (p95): {times[int(n * 0.95) - 1]:.3f} сек, "
        f"худшая: {times[-1]:.3f} сек"
    )
    print()
    if all(str(c) == "200" for c in codes):
        print("✅ Все запросы вернули 200 — ошибок не было.")
    else:
        bad = {c: v for c, v in codes.items() if str(c) != "200"}
        print(f"⚠️  Есть не-200 ответы или ошибки: {bad}")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(1)
    run(base=sys.argv[1], users=int(sys.argv[2]), workers=int(sys.argv[3]))
