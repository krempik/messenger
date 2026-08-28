# H4ck Messenger

Защищённый мессенджер с E2E шифрованием. RSA-2048 + AES-256-GCM.

## Запуск

```bash
pip install -r requirements.txt
python -m uvicorn server.main:app --host 0.0.0.0 --port 8000
```

Или через `run.bat` (Windows) — автоматически создаёт venv и устанавливает зависимости.

Иногда пины точных версий: `pip install -r requirements.lock` (генерируется `pip freeze`).

Клиент: http://localhost:8000

## Переменные окружения (продакшн)

| Переменная | Назначение |
|-----------|-----------|
| `MESSENGER_SECRET` | Секрет подписи JWT (если не задан — генерируется и хранится в `.secret_key`) |
| `VAPID_PRIVATE_KEY` | Приватный ключ VAPID для Web Push |
| `VAPID_PUBLIC_KEY` | Публичный ключ VAPID для Web Push |
| `MESSENGER_DB` | Путь к файлу БД (по умолчанию `messenger.db`) |

При обновлении версии БД мигрирует автоматически (добавляет недостающие колонки), данные сохраняются.

## CI/CD

GitHub Actions (`.github/workflows/ci.yml`) прогоняет тесты при каждом push в `main`.
Секреты для CI: `MESSENGER_SECRET`, `VAPID_PRIVATE_KEY`, `VAPID_PUBLIC_KEY`.

## Архитектура шифрования

1. При регистрации генерируется RSA-2048 ключевая пара
2. Публичный ключ → сервер, приватный → остаётся в браузере
3. При отправке сообщения:
   - Генерируется одноразовый AES-256-GCM ключ
   - Сообщение шифруется AES
   - AES-ключ шифруется RSA-публичным ключом получателя
   - На сервер → только зашифрованный текст

## API

| Метод | Эндпоинт | Описание |
|-------|-----------|----------|
| `POST` | `/api/register` | Регистрация |
| `POST` | `/api/login` | Вход (access + refresh токены) |
| `POST` | `/api/refresh` | Обновление access-токена |
| `GET` | `/api/me` | Текущий пользователь |
| `GET` | `/api/users` | Список пользователей |
| `GET` | `/api/chats` | Список чатов |
| `POST` | `/api/chats` | Создать чат |
| `GET` | `/api/chats/{chat_id}/messages` | Сообщения чата |
| `WS` | `/ws?token=...` | WebSocket для реалтайма |
| `POST` | `/api/upload` | Загрузка файла |
| `POST` | `/api/admin/login` | Вход администратора |

## Стек

- **Backend:** Python, FastAPI, SQLAlchemy, WebSocket
- **Frontend:** Vanilla JS, Web Crypto API, PWA
- **DB:** SQLite

## Лицензия

MIT
