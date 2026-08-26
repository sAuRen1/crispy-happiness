"""
Генератор хэша пароля админки.

Зачем: в .env хранится не сам пароль, а его хэш (werkzeug pbkdf2:sha256).
Если .env вдруг утечёт, атакующий получит хэш, а не пароль напрямую —
для использования хэша ему всё ещё нужно его подобрать (перебором,
что дорого при pbkdf2 с солью).

Запуск:
    python generate_admin_hash.py

Введи новый пароль (ввод не отображается на экране), скопируй
выведенную строку в .env как ADMIN_PASSWORD_HASH.
"""
import getpass

from werkzeug.security import generate_password_hash


def main() -> None:
    password = getpass.getpass("Новый пароль администратора: ")
    confirm = getpass.getpass("Повтори пароль: ")

    if password != confirm:
        print("❌ Пароли не совпадают, попробуй ещё раз.")
        return

    if len(password) < 8:
        print("❌ Слишком короткий пароль (меньше 8 символов). Возьми подлиннее.")
        return

    print("\nADMIN_PASSWORD_HASH=" + generate_password_hash(password))
    print("\nСкопируй строку выше в .env и сохрани файл.")


if __name__ == "__main__":
    main()
