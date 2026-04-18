# parser3

Парсер контактов с ролями: извлекает ФИО, должности, телефоны, email и социальные сети
с корпоративных сайтов. Поддерживает морфологическую нормализацию должностей (pymorphy3),
склонение ФИО (petrovich), и классификацию по отдельным листам Excel.

## Архитектура

- **backend** (FastAPI, Python 3.11): crawler → extractor → normalizer → classifier → deduper → exporter
- **frontend** (React + Vite + TypeScript): загрузка URL, прогресс, просмотр контактов, скачивание XLSX
- **storage**: SQLite (WAL) через aiosqlite
- **fetcher**: httpx + Playwright (SPA-fallback)

Приложение монтируется на подпуть `/parser3/` через reverse-proxy nginx.

## Запуск (Docker)

```bash
cp .env.example .env
docker compose build
docker compose up -d
```

- Backend: http://localhost:8767/health
- Frontend: http://localhost:8768/parser3/

За reverse-proxy nginx сайт доступен на `/parser3/`:
```
location /parser3/ {
    proxy_pass http://127.0.0.1:8768;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
}
```

## Разработка

```bash
# Backend
cd backend
pip install -r requirements.txt
python -m uvicorn backend.main:app --reload --port 8000

# Frontend
cd frontend
npm install
npm run dev
```

## Тесты

```bash
pip install pytest
python -m pytest tests/ -v
```

## API

Ключевые endpoint'ы:

- `POST /parser3/api/v1/tasks` — создать задачу из списка URL
- `POST /parser3/api/v1/tasks/upload` — загрузить CSV/TXT со списком URL
- `GET  /parser3/api/v1/tasks` — список задач
- `GET  /parser3/api/v1/tasks/{id}` — информация о задаче
- `GET  /parser3/api/v1/tasks/{id}/contacts` — контакты задачи
- `GET  /parser3/api/v1/tasks/{id}/download` — скачать XLSX
- `POST /parser3/api/v1/tasks/{id}/pause|resume|cancel` — управление
- `DELETE /parser3/api/v1/tasks/{id}`
- `GET  /parser3/api/v1/catalog/positions` — каталог должностей
- `GET|POST|DELETE /parser3/api/v1/blacklist`
- `WS   /parser3/ws/{id}` — прогресс real-time

## Листы Excel

1. Генеральные директора
2. Финансовые директора
3. Главные бухгалтеры
4. Главные инженеры
5. Остальные
6. Все контакты
7. Сводка
8. Отчёт качества

Замы (Заместитель …) в ролевые листы **не попадают** — уходят в «Остальные».
