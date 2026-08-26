# H4ck Messenger

Защищённый мессенджер с E2E шифрованием. RSA-2048 + AES-256-GCM.

## Запуск

```bash
pip install -r requirements.txt
python -m uvicorn server.main:app --host 0.0.0.0 --port 8000
```

Или через `run.bat` (Windows) — автоматически создаёт venv и устанавливает зависимости.

Клиент: http://localhost:8000

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
| `POST` | `/api/auth/register` | Регистрация |
| `POST` | `/api/auth/login` | Вход |
| `GET` | `/api/chats` | Список чатов |
| `POST` | `/api/chats` | Создать чат |
| `WS` | `/ws/{token}` | WebSocket для реалтайма |

## Стек

- **Backend:** Python, FastAPI, SQLAlchemy, WebSocket
- **Frontend:** Vanilla JS, Web Crypto API, PWA
- **DB:** SQLite

## Лицензия

MIT
