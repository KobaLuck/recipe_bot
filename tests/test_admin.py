import base64

from app import create_app
from models import Ingredient, db


def test_database_directories_and_admin_auth(tmp_path):
    database = tmp_path / "missing" / "nested" / "recipes.db"
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database}",
            "ADMIN_USERNAME": "admin",
            "ADMIN_PASSWORD": "test-password",
        }
    )
    assert database.exists()
    client = app.test_client()
    assert client.get("/").status_code == 200
    assert client.get("/admin/").status_code == 401
    header = {"Authorization": "Basic " + base64.b64encode(b"admin:test-password").decode()}
    assert client.get("/admin/", headers=header).status_code == 200
    assert client.get("/admin/ingredient/new/", headers=header).status_code == 200
    with app.app_context():
        db.session.add(Ingredient(name="Salt", measurement_unit="g"))
        db.session.commit()
        assert Ingredient.query.count() == 1
    assert client.get("/admin/ingredient/", headers=header).status_code == 200


def test_admin_disabled_without_credentials():
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "ADMIN_USERNAME": "",
            "ADMIN_PASSWORD": "",
        }
    )
    assert app.test_client().get("/admin/").status_code == 503
