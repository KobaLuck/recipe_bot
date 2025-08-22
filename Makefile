.PHONY: help install run-bot run-app test lint clean docker-build docker-run

help: ## Показать справку по командам
	@echo "Доступные команды:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Установить зависимости
	pip install -r requirements.txt

run-bot: ## Запустить Telegram бота
	python bot.py

run-app: ## Запустить веб-админку
	python app.py

test: ## Запустить тесты
	pytest tests/ -v

lint: ## Проверить код линтером
	flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
	flake8 . --count --exit-zero --max-complexity=10 --max-line-length=79 --statistics

clean: ## Очистить временные файлы
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -delete
	find . -type d -name "*.egg-info" -delete
	rm -rf .pytest_cache/
	rm -rf .coverage
	rm -rf htmlcov/

docker-build: ## Собрать Docker образ
	docker build -t recipes-book-bot .

docker-run: ## Запустить через Docker Compose
	docker-compose up -d

docker-stop: ## Остановить Docker контейнеры
	docker-compose down

docker-logs: ## Показать логи Docker контейнеров
	docker-compose logs -f

init-db: ## Инициализировать базу данных
	python init_db.py

venv: ## Создать виртуальное окружение
	python -m venv venv
	@echo "Виртуальное окружение создано. Активируйте его:"
	@echo "Windows: venv\\Scripts\\activate"
	@echo "Linux/MacOS: source venv/bin/activate"

dev-setup: ## Настройка для разработки
	python -m venv venv
	@echo "Виртуальное окружение создано"
	@echo "Активируйте его и выполните: make install"
