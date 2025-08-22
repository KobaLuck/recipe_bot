# app.py
from flask import Flask
from flask_admin import Admin
from flask_admin.contrib.sqla import ModelView
from settings import Config
from models import (db, User, Tag, Ingredient,
                    Recipe, RecipeIngredient, TagInRecipe, Favorite)


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    db.init_app(app)
    
    with app.app_context():
        db.create_all()

    # Инициализация Flask‑Admin внутри функции
    admin = Admin(app, name='Recipes Bot Admin', template_mode='bootstrap4')
    for model in (User, Tag, Ingredient,
                  Recipe, RecipeIngredient, TagInRecipe, Favorite):
        admin.add_view(ModelView(model, db.session))

    @app.route('/')
    def index():
        return 'Recipes Bot Admin Running'

    return app


if __name__ == '__main__':
    app = create_app()
    app.run(debug=True, host='0.0.0.0', port=5000)
