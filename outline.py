#!/usr/bin/env python3
"""Espejo local de una wiki de Outline.

Solo usa la biblioteca estándar, así que clonar y ejecutar funciona sin instalar nada.
"""

import argparse
import json
import os
import re
import shutil
import ssl
import sys
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

PAGE_SIZE = 100
STATE_DIR = ".outline"
FRONTMATTER = re.compile(r"\A---\n.*?\n---\n+", re.DOTALL)

WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}

# Solo fuera de consola: en una terminal real Python ya escribe Unicode nativo,
# pero hacia una tubería usa la codepage ANSI y destroza las tildes.
for _stream in (sys.stdout, sys.stderr):
    if not _stream.isatty():
        _stream.reconfigure(encoding="utf-8", errors="replace")


def fail(message):
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)


def project_root():
    """La raíz del proyecto: el ancestro que tenga .git, o el directorio actual."""
    current = Path.cwd().resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return current


def read_token():
    token = (os.environ.get("OUTLINE_API_TOKEN") or "").strip()
    if not token or token.startswith("<"):
        fail(
            "falta OUTLINE_API_TOKEN en el entorno. Se genera en Settings -> API Keys "
            "de tu instancia y se guarda en las variables de entorno de usuario."
        )
    return token


def read_url(argument):
    url = (argument or os.environ.get("OUTLINE_URL") or "").strip()
    if not url or url.startswith("<"):
        fail(
            "falta OUTLINE_URL en el entorno. Es la dirección de tu instancia de Outline, "
            "por ejemplo https://wiki.ejemplo.com. También se puede pasar con --url."
        )
    return url


def relaxed_context():
    context = ssl.create_default_context()
    context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return context


class Outline:
    def __init__(self, base_url, token):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.context = None

    def post(self, endpoint, payload):
        request = urllib.request.Request(
            f"{self.base_url}/api/{endpoint}",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            return self.send(request)
        except ssl.SSLCertVerificationError as error:
            # Antivirus locales que reescriben TLS incumplen VERIFY_X509_STRICT,
            # activo por defecto desde Python 3.13. Cadena y hostname siguen validandose.
            if self.context is not None or "not marked critical" not in str(error):
                fail(f"certificado rechazado por {self.base_url}: {error}")
            print(
                "aviso: TLS reescrito localmente, se desactiva VERIFY_X509_STRICT",
                file=sys.stderr,
            )
            self.context = relaxed_context()
            return self.send(request)
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", "replace")[:500]
            fail(f"{endpoint} respondió {error.code}: {body}")
        except urllib.error.URLError as error:
            fail(f"no se pudo conectar con {self.base_url}: {error.reason}")

    def send(self, request):
        try:
            with urllib.request.urlopen(request, timeout=60, context=self.context) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as error:
            if isinstance(error.reason, ssl.SSLCertVerificationError):
                raise error.reason from None
            raise

    def paginate(self, endpoint, payload):
        offset = 0
        while True:
            page = self.post(endpoint, {**payload, "limit": PAGE_SIZE, "offset": offset})
            items = page.get("data") or []
            yield from items
            total = (page.get("pagination") or {}).get("total")
            offset += len(items)
            if not items or total is None or offset >= total:
                return


def slugify(text, fallback="sin-titulo"):
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")[:60]
    if not slug or slug in WINDOWS_RESERVED:
        slug = f"{slug or fallback}-doc"
    return slug


def scope(collections, root, todas):
    """Las colecciones a bajar, y si sus rutas llevan el nombre de la colección delante.

    Sin --all, el ámbito sale de comparar el nombre del directorio del proyecto con el de
    cada colección por la misma normalización que genera las rutas. Sin coincidencia falla,
    nunca cae de vuelta a la wiki entera.
    """
    if todas:
        return collections, True
    wanted = slugify(root.name)
    matches = [c for c in collections if slugify(c["name"]) == wanted]
    if not matches:
        available = "\n".join(f"  {c['name']}" for c in collections) or "  (ninguna)"
        fail(
            f"el proyecto se llama '{root.name}' y ninguna colección se llama igual.\n"
            f"colecciones disponibles:\n{available}\n"
            "renombra el proyecto para que coincida, o pide todas con --all."
        )
    return matches, False


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def frontmatter(document_id):
    return f"---\noutline_id: {document_id}\n---\n\n"


def strip_frontmatter(text):
    return FRONTMATTER.sub("", text, count=1)


def page_body(title, text):
    stripped = (text or "").strip("\n")
    return f"# {title}\n\n{stripped}\n" if stripped else f"# {title}\n"


def manifest_path(root):
    return root / STATE_DIR / "manifest.json"


def read_manifest(root):
    path = manifest_path(root)
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_manifest(root, documents, complete):
    write(
        manifest_path(root),
        json.dumps(
            {"pullComplete": complete, "documents": documents}, indent=2, ensure_ascii=False
        )
        + "\n",
    )


def pull(client, root, todas):
    wiki = root / "wiki"
    base = root / STATE_DIR / "base"

    collections = list(client.paginate("collections.list", {}))
    selected, prefixed = scope(collections, root, todas)

    documents = {}
    for collection in selected:
        for item in client.paginate(
            "documents.list", {"collectionId": collection["id"], "statusFilter": ["published"]}
        ):
            documents[item["id"]] = item

    write_manifest(root, read_manifest(root).get("documents", {}), complete=False)

    for directory in (wiki, base):
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True)

    manifest = {}
    index_lines = []
    missing = []

    def walk(nodes, relative_dir, collection_name, depth):
        for node in nodes:
            document = documents.get(node["id"])
            if document is None:
                document = (client.post("documents.info", {"id": node["id"]}) or {}).get("data")
            if document is None:
                missing.append(node["title"])
                continue

            stem = slugify(node["title"])
            if (wiki / relative_dir / f"{stem}.md").exists():
                stem = f"{stem}-{document.get('urlId') or document['id'][:8]}"
            relative = (relative_dir / f"{stem}.md").as_posix()

            title = document.get("title") or node["title"]
            body = page_body(title, document.get("text"))
            write(wiki / relative, frontmatter(document["id"]) + body)
            write(base / relative, body)

            manifest[document["id"]] = {
                "path": relative,
                "revision": document.get("revision"),
                "collection": collection_name,
            }
            index_lines.append(f"{'  ' * depth}- [{title}](../wiki/{relative}) `{document['id']}`")

            if node.get("children"):
                walk(node["children"], relative_dir / stem, collection_name, depth + 1)

    for collection in selected:
        name = collection["name"]
        tree = client.post("collections.documents", {"id": collection["id"]}).get("data") or []
        index_lines.append(f"\n## {name}\n")
        walk(tree, Path(slugify(name)) if prefixed else Path(), name, 0)

    write(
        root / STATE_DIR / "index.md",
        "# Índice de la wiki\n\n"
        "El árbol completo en el orden real de la barra lateral de Outline, que el árbol de\n"
        "directorios no guarda. Lo genera `outline pull`; editarlo a mano no cambia nada.\n"
        + "\n".join(index_lines)
        + "\n",
    )
    write_manifest(root, manifest, complete=not missing)

    ambito = "todas las colecciones" if prefixed else f"la colección {selected[0]['name']}"
    print(f"bajados {len(manifest)} documentos de {ambito} -> {wiki}")
    if missing:
        print(f"aviso: sin contenido accesible para {len(missing)}: {', '.join(missing)}")
    return 0


def load_manifest(root):
    state = read_manifest(root)
    if not state:
        fail(f"no hay manifiesto en {manifest_path(root)}. Ejecuta 'outline pull' primero.")
    return state["documents"]


def title_of(root, entry):
    """El encabezado de nivel 1 de la página, o su ruta si falta o no lo lleva."""
    path = root / "wiki" / entry["path"]
    if path.is_file():
        body = strip_frontmatter(path.read_text(encoding="utf-8")).lstrip("\n")
        primera = body.split("\n", 1)[0]
        if primera.startswith("# "):
            return primera[2:].strip()
    return entry["path"]


def resolve(root, manifest, needle):
    """Acepta un id exacto, una ruta dentro de wiki/ o un trozo del título."""
    if needle in manifest:
        return needle
    lowered = needle.lower().replace("\\", "/").removeprefix("wiki/")
    matches = [
        document_id
        for document_id, entry in manifest.items()
        if lowered in (entry["path"].lower(), Path(entry["path"]).stem.lower())
        or lowered in title_of(root, entry).lower()
    ]
    if not matches:
        fail(
            f"'{needle}' no coincide con ninguna página. "
            f"Las páginas están en {STATE_DIR}/index.md"
        )
    if len(matches) > 1:
        options = "\n".join(f"  {i} {title_of(root, manifest[i])}" for i in matches)
        fail(f"'{needle}' coincide con {len(matches)} páginas:\n{options}")
    return matches[0]


def check(client, root, needles):
    manifest = load_manifest(root)
    targets = (
        [resolve(root, manifest, needle) for needle in needles] if needles else list(manifest)
    )
    stale = 0
    for document_id in targets:
        local = manifest[document_id]
        remote = (client.post("documents.info", {"id": document_id}) or {}).get("data") or {}
        if remote.get("revision") != local["revision"]:
            print(
                f"DESACTUALIZADO {title_of(root, local)}: local rev {local['revision']}, "
                f"remoto rev {remote.get('revision')}"
            )
            stale += 1
    if stale:
        print(f"\n{stale} documento(s) desactualizados. Sincroniza antes de escribir.")
        return 1
    print(f"{len(targets)} documento(s) al día")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="outline", description="Espejo local de una wiki de Outline"
    )
    parser.add_argument(
        "command", nargs="?", default="pull", choices=["pull", "check"],
        help="pull baja la wiki, check compara revisiones sin escribir nada",
    )
    parser.add_argument(
        "targets", nargs="*",
        help="páginas a comprobar con check, por id, ruta o título. Por defecto todas",
    )
    parser.add_argument(
        "--all", dest="todas", action="store_true",
        help="baja todas las colecciones en vez de la que se llama como el proyecto",
    )
    parser.add_argument("--url", help="dirección de la instancia. Por defecto, OUTLINE_URL")
    arguments = parser.parse_args(argv)

    client = Outline(read_url(arguments.url), read_token())
    root = project_root()
    if arguments.command == "check":
        return check(client, root, arguments.targets)
    return pull(client, root, arguments.todas)


if __name__ == "__main__":
    sys.exit(main())
