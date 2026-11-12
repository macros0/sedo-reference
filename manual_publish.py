"""Publish only a confirmed import; do not include unrelated working-tree edits."""
import os
import re
import json
import subprocess
from pathlib import Path
from sync import encode, sha, write

OWNER_LOGIN = "macros0"


def check_owner(root):
    remote = git(root, "remote", "get-url", "origin")
    if not re.fullmatch(r"(?:https://github\.com/|git@github\.com:)macros0/sedo-reference(?:\.git)?", remote, re.I):
        raise ValueError("Публикация разрешена только в репозиторий macros0/sedo-reference")
    try:
        result = subprocess.run(["gh", "api", "user", "--jq", ".login"], cwd=root, capture_output=True, check=True, timeout=30)
        login = result.stdout.decode("utf8").strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("Для публикации войдите в GitHub CLI под учётной записью владельца macros0") from exc
    if login.casefold() != OWNER_LOGIN.casefold():
        raise ValueError("Публиковать ручные спецификации может только владелец macros0")


def git(root, *args, raw=False):
    try:
        result = subprocess.run(["git", *args], cwd=root, capture_output=True, check=True, timeout=45,
                                env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never"})
        output=result.stdout.decode("utf8")
        return output if raw else output.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        detail = getattr(exc, "stderr", b"") or str(exc).encode("utf8")
        raise ValueError("Git: " + detail.decode("utf8", errors="replace")[:1200]) from exc


def publication_ready(root):
    try:
        return git(root, "branch", "--show-current") == "main" and not git(root, "status", "--porcelain")
    except ValueError:
        return False


class PendingPublication:
    def __init__(self, root, revision):
        self.root = Path(root).resolve()
        self.revision = revision
        self.commit = None
        self.expected = self.signature()
        self.receipt = self.receipt_path(self.root)
        self.persist()

    @staticmethod
    def receipt_path(root):
        path=Path(git(root,"rev-parse","--git-path","sedo-manual-publication.json"))
        return path.resolve() if path.is_absolute() else (Path(root)/path).resolve()

    def persist(self):
        write(self.receipt,encode(dict(format=1,revision=self.revision,expected=self.expected,commit=self.commit)))

    @classmethod
    def load(cls,root):
        try:receipt=cls.receipt_path(root)
        except ValueError:return None
        if not receipt.exists():return None
        data=json.loads(receipt.read_bytes())
        pending=cls.__new__(cls);pending.root=Path(root).resolve();pending.receipt=receipt
        pending.revision=data["revision"];pending.expected=tuple(data["expected"]);pending.commit=data.get("commit")
        if not pending.is_current():
            raise ValueError("Есть сохранённый импорт, но проект изменился. Проверьте изменения перед публикацией.")
        return pending

    def is_current(self):
        if self.commit:
            return git(self.root,"rev-parse","HEAD")==self.commit and not git(self.root,"status","--porcelain")
        return self.signature()==self.expected

    def signature(self):
        if git(self.root, "branch", "--show-current") != "main":
            raise ValueError("Публикация поддерживается из ветки main")
        changes = []
        raw = git(self.root, "status", "--porcelain", "-z", "--untracked-files=all", raw=True)
        for item in raw.split("\0"):
            if not item:
                continue
            name = item[3:]
            if item[:2].strip() not in ("M", "A", "??") or name.split("/")[0] not in ("state", "data", "site"):
                raise ValueError("В проекте есть посторонние изменения. Публикация остановлена.")
            path = (self.root / name).resolve()
            if not path.is_relative_to(self.root) or not path.is_file():
                raise ValueError("Некорректный путь изменения Git")
            changes.append((name, sha(path.read_bytes())))
        return git(self.root, "rev-parse", "HEAD"), sha(encode(sorted(changes)))

    def publish(self):
        check_owner(self.root)
        if self.commit:
            if git(self.root, "rev-parse", "HEAD") != self.commit or git(self.root, "status", "--porcelain"):
                raise ValueError("После сохранения проект изменился. Проверьте изменения перед публикацией.")
        else:
            if self.signature() != self.expected:
                raise ValueError("После сохранения проект изменился. Публикация остановлена.")
            remote = git(self.root, "ls-remote", "origin", "refs/heads/main").split()
            if not remote or remote[0] != self.expected[0]:
                raise ValueError("В GitHub есть новые изменения. Сохранённый импорт остаётся локально; обновите проект перед публикацией.")
            git(self.root, "add", "--", "state", "data", "site")
            if self.signature()!=self.expected:
                raise ValueError("После сохранения проект изменился. Публикация остановлена.")
            message = f"Import manual SEDO {self.revision['number']} {self.revision['environment']} v{self.revision.get('document_version') or 'unknown'}"
            git(self.root, "commit", "-m", message)
            self.commit = git(self.root, "rev-parse", "HEAD")
            self.persist()
        git(self.root, "push", "origin", "HEAD:main")
        self.receipt.unlink(missing_ok=True)
        return self.commit
