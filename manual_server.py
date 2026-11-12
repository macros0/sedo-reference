"""Local owner interface for previewing and explicitly publishing manual specifications."""
import argparse
import json
import mimetypes
import secrets
import threading
import webbrowser
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from manual_import import prepare_import
from manual_publish import PendingPublication, publication_ready


class LocalImportServer(ThreadingHTTPServer):
    def __init__(self, address, root):
        if address[0] != "127.0.0.1":
            raise ValueError("Загрузчик доступен только на 127.0.0.1")
        self.root = Path(root).resolve()
        self.token = secrets.token_urlsafe(32)
        self.preview = None
        self.preview_id = None
        self.pending = None
        self.pending_error = None
        try:self.pending=PendingPublication.load(self.root)
        except (ValueError,KeyError) as exc:self.pending_error=str(exc)
        self.lock = threading.Lock()
        super().__init__(address, ImportHandler)

    def server_close(self):
        if self.preview:
            self.preview.close()
        super().server_close()


class ImportHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, content, content_type="application/json; charset=utf-8", status=200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Sedo-Importer", "1")
        self.end_headers()
        self.wfile.write(content)

    def json(self, value, status=200):
        self.send(json.dumps(value, ensure_ascii=False).encode("utf8"), status=status)

    def valid_host(self):
        return self.headers.get("Host") == "127.0.0.1:" + str(self.server.server_port)

    def do_GET(self):
        if not self.valid_host():
            self.json(dict(error="Недопустимый адрес загрузчика"), 403)
            return
        path = unquote(urlsplit(self.path).path)
        if path == "/":
            html = Path(__file__).with_name("manual-admin.html").read_text("utf8").replace("__TOKEN__", self.server.token)
            self.send(html.encode("utf8"), "text/html; charset=utf-8")
            return
        if path=="/api/pending":
            pending=self.server.pending
            self.json(dict(pending=bool(pending),revision=pending.revision if pending else None,error=self.server.pending_error))
            return
        for prefix, directory in (("/preview/", self.server.preview.directory / "site" if self.server.preview else None), ("/reference/", self.server.root / "site")):
            if not path.startswith(prefix) or directory is None:
                continue
            relative=path[len(prefix):].replace("\\","/")
            if relative.startswith("/") or ":" in relative or "\0" in relative or ".." in relative.split("/"):
                continue
            directory = directory.resolve()
            target = (directory / relative).resolve()
            if target.is_dir():
                target /= "index.html"
            if target.is_relative_to(directory) and target.is_file():
                self.send(target.read_bytes(), mimetypes.guess_type(str(target))[0] or "application/octet-stream")
                return
        self.json(dict(error="Страница не найдена"), 404)

    def do_POST(self):
        origin = "http://127.0.0.1:" + str(self.server.server_port)
        token = self.headers.get("X-Import-Token", "")
        if not self.valid_host() or self.headers.get("Origin") != origin or not secrets.compare_digest(token, self.server.token):
            self.json(dict(error="Обновите локальную страницу загрузчика и повторите действие"), 403)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 192 * 1024 * 1024:
                raise ValueError("Пустой или слишком большой запрос")
            body = self.rfile.read(size)
            with self.server.lock:
                self.handle_action(urlsplit(self.path).path, body)
        except (ValueError, KeyError, OSError) as exc:
            self.json(dict(error=str(exc)[:1500]), 400)

    def handle_action(self, path, body):
        if path == "/api/preview":
            content_type = self.headers.get("Content-Type", "")
            if not content_type.startswith("multipart/form-data;"):
                raise ValueError("Выберите DOCX и отправьте форму загрузки")
            message = BytesParser(policy=default).parsebytes(b"Content-Type: " + content_type.encode("ascii") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body)
            fields, files = {}, {}
            if not message.is_multipart():
                raise ValueError("Не удалось прочитать форму")
            for part in message.iter_parts():
                name = part.get_param("name", header="content-disposition")
                payload = part.get_payload(decode=True) or b""
                filename = part.get_filename()
                if filename:
                    files[name] = (filename, payload)
                elif filename is None:
                    if len(payload) > 4096:
                        raise ValueError("Слишком длинное поле формы")
                    fields[name] = payload.decode("utf8")
            if "document" not in files:
                raise ValueError("Выберите DOCX")
            doc_name, document = files["document"]
            archive_name, archive = files.get("archive", ("schemas.zip", None))
            preview = prepare_import(self.server.root, document, doc_name, fields, archive, archive_name)
            if self.server.preview:
                self.server.preview.close()
            self.server.preview = preview
            self.server.preview_id = secrets.token_urlsafe(20)
            revision = preview.revision
            catalog = json.loads((preview.directory / "site/catalog-data.js").read_text("utf8").removeprefix("window.SEDO_CATALOG=").removesuffix(";"))
            event = next(e for e in catalog["updates"] if e["revision_id"] == revision["revision_id"])
            can_save=publication_ready(self.server.root)
            if self.server.pending:
                can_save=not self.server.pending.commit and self.server.pending.is_current()
            self.json(dict(id=self.server.preview_id, changed=preview.changed, revision=revision,
                           comparison=event.get("comparison"), can_save=can_save, can_publish=can_save,pending=bool(self.server.pending)))
            return
        request = json.loads(body)
        if path == "/api/confirm":
            preview = self.server.preview
            if not preview or request.get("id") != self.server.preview_id:
                raise ValueError("Сначала сформируйте предпросмотр")
            can_publish = publication_ready(self.server.root)
            if self.server.pending:
                if self.server.pending.commit and preview.changed:
                    raise ValueError("Сначала завершите публикацию ранее сохранённой редакции кнопкой повторной публикации.")
                can_publish=can_publish or self.server.pending.is_current()
            publish = request.get("publish") is True
            if preview.changed and not can_publish:
                raise ValueError("Перед сохранением нужен чистый проект в ветке main. Проверьте другие изменения и повторите предпросмотр.")
            if not preview.changed:
                self.json(dict(saved=False, published=False, message="Эта редакция уже сохранена"))
                return
            preview.save()
            self.server.preview_id = None
            self.server.pending = None
            response = dict(saved=True, published=False, can_publish=False, message="Редакция сохранена локально")
            if can_publish:
                try:
                    self.server.pending = PendingPublication(self.server.root, preview.revision)
                    response["can_publish"] = True
                except ValueError as exc:
                    response["error"] = str(exc)
            if publish and self.server.pending:
                try:
                    response.update(published=True, commit=self.server.pending.publish(), message="Редакция отправлена в GitHub. GitHub Pages обновится после проверки.")
                    self.server.pending = None
                except ValueError as exc:
                    response.update(published=False, error=str(exc), message="Редакция сохранена локально; публикация не завершена")
            self.json(response)
            return
        if path == "/api/publish":
            if not self.server.pending:
                raise ValueError("Нет подтверждённого импорта для публикации")
            commit = self.server.pending.publish()
            self.server.pending = None
            self.json(dict(saved=True, published=True, commit=commit, message="Редакция отправлена в GitHub. GitHub Pages обновится после проверки."))
            return
        raise ValueError("Неизвестное действие")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()
    try:
        server=LocalImportServer(("127.0.0.1", args.port), args.root)
    except OSError as exc:
        from urllib.request import urlopen
        url="http://127.0.0.1:"+str(args.port)+"/"
        try:
            with urlopen(url,timeout=2) as response:
                running=response.headers.get("X-Sedo-Importer")=="1"
        except OSError:running=False
        if running:
            print("Загрузчик уже запущен: "+url)
            if args.open:webbrowser.open(url)
            return
        raise SystemExit("Не удалось открыть локальный порт. Можно выбрать другой через --port. "+str(exc))
    with server:
        url = "http://127.0.0.1:" + str(server.server_port) + "/"
        print("Ручная загрузка СЭДО: " + url, flush=True)
        if args.open:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
