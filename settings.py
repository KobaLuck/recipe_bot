import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Абсолютный путь к папке проекта
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    # Путь к файлу БД
    DB_FILE = os.path.join(BASE_DIR, 'instance', 'recipes_bot.db')
    SQLALCHEMY_DATABASE_URI = f"sqlite:///{DB_FILE}"

    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SECRET_KEY = os.getenv('SECRET_KEY', 'change_me_in_prod')
    ADMIN_IDS = [
        int(i) for i in os.getenv('ADMIN_IDS', '514543014').split(',')
        if i.strip().isdigit()
    ]
    TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
