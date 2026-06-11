# parser3
# `make help` — список целей
# venv обязателен (.venv/bin/python), НЕ системный python

PY      := .venv/bin/python
PYTEST  := $(PY) -m pytest
DC_DEV  := docker compose -f docker-compose.dev.yml
BRANCH  := $(shell git rev-parse --abbrev-ref HEAD)

.DEFAULT_GOAL := help
.PHONY: help install test test-x golden update-golden fixture compare compare-dir \
        up down rebuild logs ps bshell check push push-force fetch clean

help:  ## Показать список целей
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install:  ## Создать venv и поставить зависимости
	python3 -m venv .venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r backend/requirements.txt
	$(PY) -m playwright install chromium

test:  ## Прогнать весь тест-сьют
	$(PYTEST) tests/ -q

test-x:  ## Тесты до первого падения с подробным выводом
	$(PYTEST) tests/ -x -vv

golden:  ## Прогнать только golden-кейсы
	$(PYTEST) tests/golden -q

update-golden:  ## Принять новый baseline golden (опц. K=<id> для одного кейса)
	GOLDEN_UPDATE=1 $(PYTEST) tests/golden $(if $(K),-k $(K),)

fixture:  ## Скачать HTML в фикстуру. URL=<url> [NAME=<имя>] [BROWSER=1]
	@test -n "$(URL)" || { echo "Нужен URL=https://... (опц. NAME=<имя> BROWSER=1)"; exit 1; }
	$(PY) tools/save_fixture.py $(URL) $(NAME) $(if $(BROWSER),--browser,)

compare:  ## A/B двух XLSX (old.xlsx vs new.xlsx)
	@test -n "$(OLD)" -a -n "$(NEW)" || { echo "Нужны OLD=old.xlsx NEW=new.xlsx"; exit 1; }
	$(PY) tools/compare_xlsx.py $(OLD) $(NEW)

compare-dir:  ## A/B двух папок XLSX (сумма + попарно, old/ vs new/)
	@test -n "$(DIR1)" -a -n "$(DIR2)" || { echo "Нужны DIR1=old/ DIR2=new/"; exit 1; }
	$(PY) tools/compare_xlsx.py --dir $(DIR1) $(DIR2)

up:  ## Поднять dev-стенд (backend:8769, frontend:8770)
	$(DC_DEV) up -d --build

down:  ## Остановить dev-стенд
	$(DC_DEV) down

rebuild:  ## Пересобрать с нуля
	$(DC_DEV) down
	$(DC_DEV) up -d --build

logs:  ## Логи dev-стенда
	$(DC_DEV) logs -f --tail=100

ps:  ## Статус контейнеров dev-стенда
	$(DC_DEV) ps

bshell:  ## Оболочка backend-контейнера
	docker exec -it parser3-dev-backend sh

check:  ## Проверить свежий код в контейнере
	docker exec parser3-dev-backend python -c \
	  "from backend.core.config import settings as s; print('concurrency', s.crawler_max_concurrent, '| site_timeout', s.site_total_timeout_sec, '| dns_precheck', s.dns_precheck, '| connect_timeout', s.crawler_connect_timeout_sec)"

push:  ## Запушить текущую ветку с -u (сейчас: exp/extractor)
	git push -u origin $(BRANCH)

push-force:  ## Форс-пуш текущей ветки (--force-with-lease: перезапишет удалёнку, но не затрёт чужое)
	git push --force-with-lease -u origin $(BRANCH)

fetch:  ## Забрать свежую ветку (на сервере, затрёт локальные правки)
	git fetch origin
	git checkout $(BRANCH)
	git reset --hard origin/$(BRANCH)

clean:  ## Удалить __pycache__ и .pyc
	find . -path ./.venv -prune -o -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -path ./.venv -prune -o -type f -name '*.pyc' -delete 2>/dev/null || true
