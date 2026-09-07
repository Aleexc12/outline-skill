#!/usr/bin/env python3
"""Espejo local de una wiki de Outline.

Solo usa la biblioteca estándar, así que clonar y ejecutar funciona sin instalar nada.
"""

import argparse
import json
import os
import re
import ssl
import sys
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

PAGE_SIZE = 100
STATE_DIR = ".outline"
FRONTMATTER = re.compile(r"\A---\n.*?\n---\n+", re.DOTALL)
IDENTITY = re.compile(r"\A---\s*\noutline_id:\s*(\S+)\s*\n---")

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


def matching_collections(collections, root, todas):
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


def discard(path, stop):
    """Borra un fichero y, por encima, las carpetas que se queden vacías."""
    if not path.is_file():
        return
    path.unlink()
    for parent in path.parents:
        if parent == stop or any(parent.iterdir()):
            return
        try:
            parent.rmdir()
        except OSError:
            return


def plural(count, one, many):
    return f"{count} {one if count == 1 else many}"


def section(title, pages):
    if not pages:
        return
    print(f"{title} ({len(pages)}):")
    for page in pages:
        print(f"  {page}")
    print()


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


@dataclass
class Page:
    """Una página del remoto con la ruta que le toca en el disco."""

    id: str
    title: str
    body: str
    revision: int
    path: str
    collection: dict
    depth: int


def remote_documents(client, selected):
    documents = {}
    for collection in selected:
        for item in client.paginate(
            "documents.list", {"collectionId": collection["id"], "statusFilter": ["published"]}
        ):
            documents[item["id"]] = item
    return documents


@dataclass
class Scope:
    """Las colecciones que le tocan a este proyecto, con sus documentos remotos."""

    collections: list
    prefixed: bool
    documents: dict

    @classmethod
    def read(cls, client, root, todas):
        available = list(client.paginate("collections.list", {}))
        selected, prefixed = matching_collections(available, root, todas)
        return cls(selected, prefixed, remote_documents(client, selected))

    @property
    def ids(self):
        return {collection["id"] for collection in self.collections}

    def label(self):
        if self.prefixed:
            return "todas las colecciones"
        return f"la colección {self.collections[0]['name']}"


def remote_pages(client, scope):
    """Las páginas del ámbito, en el orden de la barra lateral, con su ruta en el disco.

    Devuelve además los nodos cuyo contenido no se pudo leer, que no son páginas
    borradas y hay que proteger de la limpieza.
    """
    pages, missing, taken = [], [], set()

    def walk(nodes, relative_dir, collection, depth):
        for node in nodes:
            document = scope.documents.get(node["id"])
            if document is None:
                document = (client.post("documents.info", {"id": node["id"]}) or {}).get("data")
            if document is None:
                missing.append(node)
                continue

            stem = slugify(node["title"])
            if (relative_dir / f"{stem}.md").as_posix() in taken:
                stem = f"{stem}-{document.get('urlId') or document['id'][:8]}"
            relative = (relative_dir / f"{stem}.md").as_posix()
            taken.add(relative)

            title = document.get("title") or node["title"]
            pages.append(
                Page(
                    id=document["id"],
                    title=title,
                    body=page_body(title, document.get("text")),
                    revision=document.get("revision"),
                    path=relative,
                    collection=collection,
                    depth=depth,
                )
            )
            walk(node.get("children") or [], relative_dir / stem, collection, depth + 1)

    for collection in scope.collections:
        tree = client.post("collections.documents", {"id": collection["id"]}).get("data") or []
        root = Path(slugify(collection["name"])) if scope.prefixed else Path()
        walk(tree, root, collection, 0)
    return pages, missing


def pages_on_disk(directory):
    return sorted(directory.rglob("*.md")) if directory.is_dir() else []


def shadow(directory):
    """Los cuerpos de `.outline/base/`, indexados por su ruta."""
    return {
        path.relative_to(directory).as_posix(): path.read_text(encoding="utf-8")
        for path in pages_on_disk(directory)
    }


@dataclass
class Mirror:
    """La copia local de `wiki/`, leída entera antes de escribir nada encima."""

    bodies: dict
    ids: dict

    @classmethod
    def read(cls, wiki):
        bodies, ids = {}, {}
        for path in pages_on_disk(wiki):
            relative = path.relative_to(wiki).as_posix()
            text = path.read_text(encoding="utf-8", errors="replace")
            match = IDENTITY.match(text)
            if match:
                ids[relative] = match.group(1)
            bodies[relative] = strip_frontmatter(text)
        return cls(bodies, ids)


def changed(local, base, remote):
    """Si la copia local tiene algo que no está ni en la base ni en el remoto."""
    return local != base and local != remote


def advanced(page, entry, base):
    """Si Outline se movió respecto de la base que dejó el último pull."""
    if page is None:
        return True
    if base is None:
        return False
    return page.body != base or page.path != entry["path"]


def write_index(root, entries):
    lines, previous = [], None
    for collection, depth, title, relative, document_id in entries:
        if collection != previous:
            lines.append(f"\n## {collection}\n")
            previous = collection
        lines.append(f"{'  ' * depth}- [{title}](../wiki/{relative}) `{document_id}`")
    write(
        root / STATE_DIR / "index.md",
        "# Índice de la wiki\n\n"
        "El árbol completo en el orden real de la barra lateral de Outline, que el árbol de\n"
        "directorios no guarda. Lo genera `outline pull`; editarlo a mano no cambia nada.\n"
        + "\n".join(lines)
        + "\n",
    )


def pull(client, root, todas):
    wiki = root / "wiki"
    shadow_dir = root / STATE_DIR / "base"

    scope = Scope.read(client, root, todas)
    previous = read_manifest(root).get("documents", {})
    write_manifest(root, previous, complete=False)

    pages, missing = remote_pages(client, scope)
    untouched = {node["id"] for node in missing}
    manifest = {
        i: e
        for i, e in previous.items()
        if e.get("collectionId") not in scope.ids or i in untouched
    }

    mirror = Mirror.read(wiki)
    shadowed = shadow(shadow_dir)
    destinations = {page.path for page in pages}

    def release(directory, relative):
        """Deja libre la ruta vieja de una página que se movió, si nadie más la ocupa."""
        if relative not in destinations:
            discard(directory / relative, directory)

    skipped, updated, removed, entries = [], 0, 0, []

    for page in pages:
        entry = previous.get(page.id)
        was_at = entry["path"] if entry else page.path
        local = mirror.bodies.get(was_at)

        if changed(local, shadowed.get(was_at), page.body):
            skipped.append(f"wiki/{was_at}")
            if entry:
                manifest[page.id] = entry
            lives_at = was_at
        else:
            lives_at = page.path
            if local != page.body or was_at != lives_at:
                updated += 1
            write(wiki / lives_at, frontmatter(page.id) + page.body)
            write(shadow_dir / lives_at, page.body)
            if was_at != lives_at:
                release(wiki, was_at)
                release(shadow_dir, was_at)
            manifest[page.id] = {
                "path": lives_at,
                "revision": page.revision,
                "collection": page.collection["name"],
                "collectionId": page.collection["id"],
            }
        entries.append((page.collection["name"], page.depth, page.title, lives_at, page.id))

    seen = {page.id for page in pages} | untouched
    for document_id, entry in previous.items():
        if document_id in seen or entry.get("collectionId") not in scope.ids:
            continue
        if changed(mirror.bodies.get(entry["path"]), shadowed.get(entry["path"]), None):
            skipped.append(f"wiki/{entry['path']} (borrada en Outline)")
            manifest[document_id] = entry
        else:
            release(wiki, entry["path"])
            release(shadow_dir, entry["path"])
            removed += 1

    write_index(root, entries)
    write_manifest(root, manifest, complete=not missing)

    partes = [f"{plural(len(pages), 'página', 'páginas')} en {scope.label()}"]
    if updated:
        partes.append(plural(updated, "actualizada", "actualizadas"))
    if removed:
        partes.append(plural(removed, "borrada aquí", "borradas aquí"))
    if skipped:
        partes.append(f"{len(skipped)} sin tocar")
    if not updated and not removed and not skipped:
        partes.append("todo al día")
    print(f"{', '.join(partes)} -> {wiki}")
    section("sin tocar por tener cambios locales", skipped)
    if missing:
        titles = ", ".join(node["title"] for node in missing)
        print(f"aviso: sin contenido accesible para {len(missing)}: {titles}")
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


def status(client, root, todas):
    wiki = root / "wiki"

    manifest = load_manifest(root)
    scope = Scope.read(client, root, todas)
    pages, missing = remote_pages(client, scope)
    mirror = Mirror.read(wiki)
    shadowed = shadow(root / STATE_DIR / "base")

    remote = {page.id: page for page in pages}
    unreadable = {node["id"] for node in missing}
    tracked = {entry["path"] for entry in manifest.values()}
    located = {document_id: relative for relative, document_id in mirror.ids.items()}

    limpias, sucias, adelantadas, conflictos = 0, [], [], []
    for document_id, entry in manifest.items():
        if entry.get("collectionId") not in scope.ids or document_id in unreadable:
            continue
        page = remote.get(document_id)
        base = shadowed.get(entry["path"])
        moved_to = located.get(document_id)
        moved = moved_to is not None and moved_to != entry["path"]
        if moved:
            tracked.add(moved_to)
        local = mirror.bodies.get(moved_to if moved else entry["path"])

        mine = moved or changed(local, base, page.body if page else None)
        theirs = advanced(page, entry, base)

        etiqueta = f"wiki/{moved_to} (movida desde {entry['path']})" if moved else f"wiki/{entry['path']}"
        if page is None:
            etiqueta += " (borrada en Outline)"
        elif local is None:
            etiqueta += " (borrada aquí)"

        if mine and theirs:
            conflictos.append(etiqueta)
        elif mine:
            sucias.append(etiqueta)
        elif theirs:
            adelantadas.append(etiqueta)
        else:
            limpias += 1

    # Un fichero fuera del manifiesto es una página nueva o una que el pull nunca llegó a adoptar.
    nuevas = []
    for relative, body in mirror.bodies.items():
        if relative in tracked:
            continue
        page = remote.get(mirror.ids.get(relative))
        if page is None:
            nuevas.append(f"wiki/{relative}")
        elif body != page.body:
            sucias.append(f"wiki/{relative}")
        else:
            limpias += 1

    section("sucias, cambiadas aquí desde el último pull", sorted(sucias))
    section("remota adelantada, cambiadas en Outline", sorted(adelantadas))
    section("en conflicto, cambiadas aquí y en Outline", sorted(conflictos))
    section("nuevas, todavía no están en Outline", sorted(nuevas))

    print(
        f"{plural(limpias, 'limpia', 'limpias')}, {plural(len(sucias), 'sucia', 'sucias')}, "
        f"{len(adelantadas)} con la remota adelantada y {len(conflictos)} en conflicto "
        f"en {scope.label()}"
    )
    if missing:
        titles = ", ".join(node["title"] for node in missing)
        print(f"aviso: sin contenido accesible para {len(missing)}: {titles}")
    return 0


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
        "command", nargs="?", default="pull", choices=["pull", "status", "check"],
        help="pull baja la wiki, status dice en qué estado estás, "
             "check compara revisiones sin escribir nada",
    )
    parser.add_argument(
        "targets", nargs="*",
        help="páginas a comprobar con check, por id, ruta o título. Por defecto todas",
    )
    parser.add_argument(
        "--all", dest="todas", action="store_true",
        help="trabaja con todas las colecciones en vez de la que se llama como el proyecto",
    )
    parser.add_argument("--url", help="dirección de la instancia. Por defecto, OUTLINE_URL")
    arguments = parser.parse_args(argv)

    client = Outline(read_url(arguments.url), read_token())
    root = project_root()
    if arguments.command == "check":
        return check(client, root, arguments.targets)
    if arguments.command == "status":
        return status(client, root, arguments.todas)
    return pull(client, root, arguments.todas)


if __name__ == "__main__":
    sys.exit(main())
