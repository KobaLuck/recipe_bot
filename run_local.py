"""Run the Recipes backend and bot together on this computer.

The backend checkout is kept separate; its source is not modified.
"""

import argparse
import json
import os
import secrets
import signal
import subprocess
import sys
import threading
import types
from pathlib import Path
from wsgiref.simple_server import WSGIRequestHandler, make_server

BASE_DIR = Path(__file__).resolve().parent


def configure_backend(source, instance):
    sys.path.insert(0, str(source))
    from config import settings as source_settings
    from django.conf import settings

    instance.mkdir(parents=True, exist_ok=True)
    secret_file = instance / "site_secret"
    if not secret_file.exists():
        fd = os.open(secret_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(secrets.token_urlsafe(48))
    config = {key: getattr(source_settings, key) for key in dir(source_settings) if key.isupper()}
    config.update(
        SECRET_KEY=secret_file.read_text(),
        DEBUG=False,
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": str(instance / "site.sqlite3"),
            }
        },
        MEDIA_ROOT=str(instance / "media"),
        ALLOWED_HOSTS=["127.0.0.1", "localhost"],
        ROOT_URLCONF="local_recipes_urls",
    )
    settings.configure(**config)
    import django
    from django.core.management import call_command

    django.setup()
    call_command("migrate", verbosity=0)
    from config.urls import urlpatterns
    from django.urls import re_path
    from django.views.static import serve

    local_urls = types.ModuleType("local_recipes_urls")
    local_urls.urlpatterns = [
        *urlpatterns,
        re_path(r"^media/(?P<path>.*)$", serve, {"document_root": settings.MEDIA_ROOT}),
    ]
    sys.modules["local_recipes_urls"] = local_urls
    from recipes.models import Ingredient, Tag

    data = source.parent / "data" / "ingredients.json"
    if data.exists() and not Ingredient.objects.exists():
        Ingredient.objects.bulk_create(
            [Ingredient(**item) for item in json.loads(data.read_text())], ignore_conflicts=True
        )
    for name, slug in [("Завтрак", "breakfast"), ("Обед", "lunch"), ("Ужин", "dinner")]:
        Tag.objects.get_or_create(slug=slug, defaults={"name": name})
    from django.core.wsgi import get_wsgi_application

    return get_wsgi_application()


class QuietHandler(WSGIRequestHandler):
    def log_message(self, *args):
        # API login bodies and authorization headers must never enter logs.
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-source", type=Path, required=True, help="Path to Recipes/backend")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--instance", type=Path, default=BASE_DIR / "instance" / "site")
    args = parser.parse_args()
    if not (args.site_source / "manage.py").is_file():
        parser.error("--site-source must point to Recipes/backend")
    instance = args.instance.resolve()
    wsgi = configure_backend(args.site_source.resolve(), instance)
    server = make_server("127.0.0.1", args.port, wsgi, handler_class=QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    environment = {**os.environ, "SITE_API_BASE": f"http://127.0.0.1:{args.port}/api/"}
    process = subprocess.Popen(
        [sys.executable, "-u", str(BASE_DIR / "bot.py")], cwd=BASE_DIR, env=environment
    )
    (instance / "supervisor.pid").write_text(str(os.getpid()))
    print(f"API ready on http://127.0.0.1:{args.port}/api/; bot started.", flush=True)

    def stop(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        status = process.wait()
    except KeyboardInterrupt:
        status = 0
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        server.shutdown()
        server.server_close()
        (instance / "supervisor.pid").unlink(missing_ok=True)
    return status


if __name__ == "__main__":
    sys.exit(main())
