"""Optional local admin for the legacy SQLite database (not the site's DB)."""

import hmac
from pathlib import Path

from flask import Flask, Response, request
from flask_admin import Admin
from flask_admin.contrib.sqla import ModelView
from sqlalchemy.engine import make_url

from models import (
    Favorite,
    Ingredient,
    Recipe,
    RecipeIngredient,
    ShoppingCart,
    Tag,
    TagInRecipe,
    User,
    db,
)
from settings import Config


def create_app(config=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if config:
        app.config.update(config)
    uri = make_url(app.config["SQLALCHEMY_DATABASE_URI"])
    if uri.drivername.startswith("sqlite") and uri.database and uri.database != ":memory:":
        database = Path(uri.database)
        if not database.is_absolute():
            database = Path(app.instance_path) / database
        database.parent.mkdir(parents=True, exist_ok=True)
    db.init_app(app)
    with app.app_context():
        db.create_all()

    @app.before_request
    def protect_admin():
        if request.path == "/admin" or request.path.startswith("/admin/"):
            username = app.config.get("ADMIN_USERNAME", "")
            password = app.config.get("ADMIN_PASSWORD", "")
            if not username or not password:
                return Response("Настройте ADMIN_USERNAME и ADMIN_PASSWORD.", status=503)
            auth = request.authorization
            if (
                not auth
                or not hmac.compare_digest(auth.username or "", username)
                or not hmac.compare_digest(auth.password or "", password)
            ):
                return Response(
                    "Требуется вход.",
                    status=401,
                    headers={"WWW-Authenticate": 'Basic realm="Recipes Admin"'},
                )
        return None

    admin = Admin(app, name="Recipes Bot Admin", template_mode="bootstrap4")
    for model in (
        User,
        Tag,
        Ingredient,
        Recipe,
        RecipeIngredient,
        TagInRecipe,
        Favorite,
        ShoppingCart,
    ):
        admin.add_view(ModelView(model, db.session))

    @app.route("/")
    def index():
        return "Recipes Bot Admin Running"

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=app.config["DEBUG"], host=app.config["HOST"], port=app.config["PORT"])
