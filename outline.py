#!/usr/bin/env python3
"""Espejo local de una wiki de Outline.

Solo usa la biblioteca estándar, así que clonar y ejecutar funciona sin instalar nada.
"""

import argparse
import difflib
import json
import os
import re
import ssl
import sys
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

PAGE_SIZE = 100
STATE_DIR = ".outline"
FRONTMATTER = re.compile(r"\A---\n(?P<meta>.*?)\n---\n+", re.DOTALL)
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


def ask(question):
    """Lo que conteste la persona, o nada si no hay ninguna al teclado."""
    if not sys.stdin.isatty():
        return None
    try:
        return input(question)
    except EOFError:
        return None


def announce(pages):
    """Lista lo que se va a borrar antes de tocar nada, para poder abortar a tiempo."""
    print("estas páginas están en el manifiesto del último pull y ya no están en el disco:")
    for page in pages:
        print(f"  {page}")
    print()
    print("borrarlas las manda a la papelera de Outline, así que hay vuelta atrás.")


def agree():
    """Si la persona da permiso para borrar, o nada si no hay ninguna al teclado."""
    answer = ask('escribe "si" para borrarlas: ')
    if answer is None:
        print("nadie contesta, así que no se borra nada. Con 'outline push --yes' se borran.")
        return None
    print()
    return answer.strip().lower() in {"s", "si", "sí"}


def frontmatter(document_id):
    return f"---\noutline_id: {document_id}\n---\n\n"


def strip_frontmatter(text):
    """Un `---` que abre el cuerpo es contenido, así que solo se quita el frontmatter propio."""
    match = FRONTMATTER.match(text)
    if match and "outline_id:" in match.group("meta"):
        return text[match.end():]
    return text


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


def fetch_document(client, document_id):
    return (client.post("documents.info", {"id": document_id}) or {}).get("data")


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
    def only_collections(cls, client, root, todas):
        """El ámbito sin bajarse los documentos, para lo que solo necesita la colección."""
        available = list(client.paginate("collections.list", {}))
        selected, prefixed = matching_collections(available, root, todas)
        return cls(selected, prefixed, {})

    @classmethod
    def read(cls, client, root, todas):
        scope = cls.only_collections(client, root, todas)
        scope.documents = remote_documents(client, scope.collections)
        return scope

    def by_id(self, collection_id):
        return next((c for c in self.collections if c["id"] == collection_id), None)

    @property
    def ids(self):
        return {collection["id"] for collection in self.collections}

    @property
    def names(self):
        return {collection["name"] for collection in self.collections}

    def covers(self, entry):
        if "collectionId" in entry:
            return entry["collectionId"] in self.ids
        return entry.get("collection") in self.names

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
                document = fetch_document(client, node["id"])
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


def advanced_by_body(page, entry, base):
    """Si Outline se movió, mirando el cuerpo y la ruta que trae el árbol del pull."""
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
    manifest = {i: e for i, e in previous.items() if not scope.covers(e) or i in untouched}

    mirror = Mirror.read(wiki)
    shadowed = shadow(shadow_dir)
    destinations = {page.path for page in pages}

    def release(directory, relative):
        """Deja libre la ruta vieja de una página que se movió, si nadie más la ocupa."""
        if relative not in destinations:
            discard(directory / relative, directory)

    skipped, blocked, updated, removed, entries = [], [], 0, 0, []

    for page in pages:
        entry = previous.get(page.id)
        was_at = entry["path"] if entry else page.path
        local = mirror.bodies.get(was_at)

        if changed(local, shadowed.get(was_at), page.body):
            skipped.append(f"wiki/{was_at}")
            stays = True
        elif page.path != was_at and changed(
            mirror.bodies.get(page.path), shadowed.get(page.path), page.body
        ):
            blocked.append(f"wiki/{page.path}, con {page.title} todavía en wiki/{was_at}")
            stays = True
        else:
            stays = False

        if stays:
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
        if document_id in seen or not scope.covers(entry):
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
    if blocked:
        partes.append(f"{len(blocked)} sin mover")
    if not updated and not removed and not skipped and not blocked:
        partes.append("todo al día")
    print(f"{', '.join(partes)} -> {wiki}")
    section("sin tocar por tener cambios locales", skipped)
    section("sin mover porque el destino tiene cambios locales", blocked)
    if missing:
        titles = ", ".join(node["title"] for node in missing)
        print(f"aviso: sin contenido accesible para {len(missing)}: {titles}")
    return 0


def load_state(root):
    state = read_manifest(root)
    if not state:
        fail(f"no hay manifiesto en {manifest_path(root)}. Ejecuta 'outline pull' primero.")
    return state


def load_manifest(root):
    return load_state(root)["documents"]


def title_of(root, relative):
    """El encabezado de nivel 1 de la página, o su ruta si falta o no lo lleva."""
    path = root / "wiki" / relative
    if path.is_file():
        body = strip_frontmatter(path.read_text(encoding="utf-8")).lstrip("\n")
        primera = body.split("\n", 1)[0]
        if primera.startswith("# "):
            return primera[2:].strip()
    return relative


def located(mirror, manifest):
    """Dónde está hoy el fichero de cada página, que no siempre es lo que dice el manifiesto.

    Manda el frontmatter: una página que has arrastrado a otra carpeta vive donde la dejaste,
    y el manifiesto no se entera hasta el `push`.
    """
    return {
        **{document_id: entry["path"] for document_id, entry in manifest.items()},
        **{document_id: relative for relative, document_id in mirror.ids.items()},
    }


def find_page(root, manifest, needle, at):
    """Acepta un id exacto, una ruta dentro de wiki/ o un trozo del título."""
    if needle in manifest:
        return needle
    lowered = needle.lower().replace("\\", "/").removeprefix("wiki/")

    def hits(document_id):
        relative = at[document_id]
        names = (relative.lower(), Path(relative).stem.lower())
        return lowered in names or lowered in title_of(root, relative).lower()

    matches = [document_id for document_id in manifest if hits(document_id)]
    if not matches:
        fail(
            f"'{needle}' no coincide con ninguna página. "
            f"Las páginas están en {STATE_DIR}/index.md"
        )
    if len(matches) > 1:
        options = "\n".join(f"  {i} {title_of(root, at[i])}" for i in matches)
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
    lives = located(mirror, manifest)

    limpias, sucias, adelantadas, conflictos = 0, [], [], []
    for document_id, entry in manifest.items():
        if not scope.covers(entry) or document_id in unreadable:
            continue
        page = remote.get(document_id)
        base = shadowed.get(entry["path"])
        moved_to = lives[document_id]
        moved = moved_to != entry["path"]
        if moved:
            tracked.add(moved_to)
        local = mirror.bodies.get(moved_to)

        mine = moved or changed(local, base, page.body if page else None)
        theirs = advanced_by_body(page, entry, base)

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
    if conflictos:
        print("los dos lados de un conflicto se ven con 'outline diff'")
    if missing:
        titles = ", ".join(node["title"] for node in missing)
        print(f"aviso: sin contenido accesible para {len(missing)}: {titles}")
    return 0


def check(client, root, needles):
    manifest = load_manifest(root)
    lives = located(Mirror.read(root / "wiki"), manifest)
    targets = (
        [find_page(root, manifest, needle, lives) for needle in needles]
        if needles
        else list(manifest)
    )
    stale = 0
    for document_id in targets:
        local = manifest[document_id]
        remote = fetch_document(client, document_id) or {}
        if remote.get("revision") != local["revision"]:
            print(
                f"DESACTUALIZADO {title_of(root, lives[document_id])}: "
                f"local rev {local['revision']}, remoto rev {remote.get('revision')}"
            )
            stale += 1
    if stale:
        print(f"\n{stale} documento(s) desactualizados. Sincroniza antes de escribir.")
        return 1
    print(f"{len(targets)} documento(s) al día")
    return 0


def split_title(body):
    """El título y el cuerpo que van a la API, con el encabezado de nivel 1 ya fuera."""
    body = body.lstrip("\n")
    first, _, rest = body.partition("\n")
    if first.startswith("# "):
        return first[2:].strip(), rest.strip("\n")
    return None, body.strip("\n")


def refuse_duplicates(identities):
    """Dos ficheros con la misma identidad son una copia a la que no le quitaron el id."""
    repeated = {}
    for relative in sorted(identities):
        if identities[relative] is None:
            continue
        repeated.setdefault(identities[relative], []).append(f"wiki/{relative}")
    conflicting = {i: paths for i, paths in repeated.items() if len(paths) > 1}
    if not conflicting:
        return
    detail = "\n".join(
        f"  {i}\n" + "\n".join(f"    {path}" for path in paths)
        for i, paths in conflicting.items()
    )
    fail(
        "hay páginas que comparten identidad y no se puede saber cuál manda, "
        f"así que no se sube nada:\n{detail}\n"
        "quita el outline_id del frontmatter de la copia, o sácala de la ruta que el "
        "manifiesto ya da por ocupada, para que el push la cree aparte."
    )


def collection_for(relative, scope):
    """La colección que le toca a un fichero por su ruta, o None si la ruta no lo dice."""
    if not scope.prefixed:
        return scope.collections[0]
    head, _, rest = relative.partition("/")
    if not rest:
        return None
    return next((c for c in scope.collections if slugify(c["name"]) == head), None)


def parent_path(relative, scope):
    """La ruta de la página de la que cuelga un fichero, o None si cuelga de la colección."""
    parts = relative.split("/")
    if len(parts) == (2 if scope.prefixed else 1):
        return None
    return "/".join(parts[:-1]) + ".md"


def level_size(relative, mirror):
    """Cuántas páginas hay en el nivel de un fichero, contándolo a él."""
    folder = relative.rpartition("/")[0]
    return sum(1 for other in mirror.bodies if other.rpartition("/")[0] == folder)


def advanced_by_revision(remote, entry, base, remote_body):
    """Si Outline se movió, mirando la revisión que devuelve la página recién pedida."""
    if entry.get("revision") is None:
        return remote_body != base
    return remote.get("revision") != entry["revision"]


def remember(root, manifest, relative, document, collection):
    """Da la página por sincronizada, con la base en lo que Outline acaba de devolver."""
    write(
        root / STATE_DIR / "base" / relative,
        page_body(document["title"], document.get("text")),
    )
    manifest[document["id"]] = {
        "path": relative,
        "revision": document.get("revision"),
        "collection": collection["name"],
        "collectionId": collection["id"],
    }


@dataclass
class Tally:
    """Un cubo del resumen del push: cómo se cuenta, bajo qué encabezado se lista y si falla."""

    field: str
    one: str
    many: str
    heading: str = ""
    failure: bool = False


TALLIES = [
    Tally("uploaded", "página subida", "páginas subidas", "subidas"),
    Tally("created", "creada", "creadas", "creadas"),
    Tally("moved", "movida", "movidas", "movidas"),
    Tally("reformatted", "reformateada por Outline", "reformateadas por Outline",
          "reformateadas por Outline al guardarlas, así que su fichero local ha cambiado"),
    Tally("overtaken", "movida con la remota adelantada", "movidas con la remota adelantada",
          "movidas, pero Outline tiene texto que no has visto. Pásales un 'outline pull'"),
    Tally("deleted", "borrada", "borradas", "borradas, a la papelera de Outline"),
    Tally("settled", "ya estaba en Outline", "ya estaban en Outline"),
    Tally("declined", "que no has querido borrar", "que no has querido borrar",
          "sin borrar, porque has dicho que no"),
    Tally("clashed", "en conflicto", "en conflicto",
          "en conflicto, la remota se adelantó desde el último pull", True),
    Tally("unsure", "sin comparar", "sin comparar",
          "sin comparar, así que no se han tocado", True),
    Tally("rejected", "sin crear", "sin crear",
          "sin crear, porque la ruta o el fichero no dicen dónde va", True),
    Tally("unmoved", "sin mover", "sin mover",
          "sin mover, porque la ruta nueva no dice dónde van", True),
    Tally("spared", "sin borrar", "sin borrar", "sin borrar, así que siguen en Outline", True),
]


@dataclass
class Pusher:
    """Una pasada de push: de dónde lee, contra qué compara y qué lleva hecho."""

    client: object
    root: Path
    mirror: Mirror
    shadowed: dict
    scope: Scope
    manifest: dict
    pages_by_path: dict
    uploaded: list = field(default_factory=list)
    created: list = field(default_factory=list)
    moved: list = field(default_factory=list)
    reformatted: list = field(default_factory=list)
    overtaken: list = field(default_factory=list)
    deleted: list = field(default_factory=list)
    settled: list = field(default_factory=list)
    declined: list = field(default_factory=list)
    clashed: list = field(default_factory=list)
    unsure: list = field(default_factory=list)
    rejected: list = field(default_factory=list)
    unmoved: list = field(default_factory=list)
    spared: list = field(default_factory=list)
    note: str = ""

    def update(self, relative, document_id, entry):
        """Sube una página que Outline ya conoce, si nadie se ha adelantado.

        Devuelve si queda en un estado del que fiarse, que es lo que permite moverla después.
        """
        local = self.mirror.bodies[relative]
        was_at = entry["path"] if entry else relative
        base = self.shadowed.get(was_at)
        if local == base:
            return True
        remote = fetch_document(self.client, document_id)
        if remote is None:
            self.unsure.append(f"wiki/{relative}, sin contenido accesible en Outline")
            return False
        collection = self.scope.by_id(remote.get("collectionId"))
        if collection is None:
            self.unsure.append(
                f"wiki/{relative}, en una colección fuera de {self.scope.label()}"
            )
            return False
        remote_body = page_body(remote["title"], remote.get("text"))
        if local == remote_body:
            remember(self.root, self.manifest, was_at, remote, collection)
            self.settled.append(f"wiki/{relative}")
            return True
        if entry is None or base is None:
            self.unsure.append(
                f"wiki/{relative}, sin base con la que comparar. "
                "Acepta el estado de Outline con 'outline resolve' y vuelve a intentarlo"
            )
            return False
        if advanced_by_revision(remote, entry, base, remote_body):
            self.clashed.append(f"wiki/{relative}")
            return False
        title, body = split_title(local)
        payload = {"id": document_id, "text": body}
        if title:
            payload["title"] = title
        document = (self.client.post("documents.update", payload) or {}).get("data")
        if document is None:
            self.unsure.append(f"wiki/{relative}, sin respuesta de Outline al escribirla")
            return False
        remember(self.root, self.manifest, was_at, document, collection)
        self.settle_body(relative, document)
        self.uploaded.append(f"wiki/{relative}")
        return True

    def create(self, relative):
        """Crea en Outline una página que hasta ahora solo existía en el disco."""
        title, body = split_title(self.mirror.bodies[relative])
        collection = collection_for(relative, self.scope)
        parent = parent_path(relative, self.scope)
        if title is None:
            self.rejected.append(
                f"wiki/{relative}, sin el encabezado de nivel 1 que da el título"
            )
            return
        if collection is None:
            self.rejected.append(f"wiki/{relative}, fuera de ninguna colección")
            return
        if parent is not None and parent not in self.pages_by_path:
            self.rejected.append(
                f"wiki/{relative}, que cuelga de wiki/{parent} y esa página no existe"
            )
            return
        payload = {
            "title": title,
            "text": body,
            "collectionId": collection["id"],
            "publish": True,
        }
        if parent:
            payload["parentDocumentId"] = self.pages_by_path[parent]
        document = (self.client.post("documents.create", payload) or {}).get("data")
        if document is None:
            self.rejected.append(f"wiki/{relative}, sin respuesta de Outline al crearla")
            return
        document["revision"] = self.place_last(document, relative, collection, parent)
        self.pages_by_path[relative] = document["id"]
        self.settle_body(relative, document)
        remember(self.root, self.manifest, relative, document, collection)
        self.created.append(f"wiki/{relative}")

    def place_last(self, document, relative, collection, parent):
        """Outline la cuelga al principio del nivel, y moverla al final sube su revisión."""
        movimiento = {
            "id": document["id"],
            "collectionId": collection["id"],
            "index": level_size(relative, self.mirror),
        }
        if parent:
            movimiento["parentDocumentId"] = self.pages_by_path[parent]
        answer = (self.client.post("documents.move", movimiento) or {}).get("data") or {}
        moved = next(
            (d for d in answer.get("documents", []) if d["id"] == document["id"]), None
        )
        return (moved or document).get("revision")

    def settle_body(self, relative, document):
        """Deja el fichero local con el texto que Outline ha guardado, que puede no ser el mío.

        Outline reescribe el Markdown a su forma canónica al guardarlo, y sin esto el fichero
        no vuelve a coincidir con su base nunca más.
        """
        body = page_body(document["title"], document.get("text"))
        if self.mirror.bodies[relative] != body:
            self.mirror.bodies[relative] = body
            self.reformatted.append(f"wiki/{relative}")
        write(self.root / "wiki" / relative, frontmatter(document["id"]) + body)

    def nesting(self, relative):
        """Dónde cuelga un fichero en Outline según su ruta: colección, ruta madre y su id."""
        collection = collection_for(relative, self.scope)
        parent = parent_path(relative, self.scope)
        return collection, parent, (self.pages_by_path.get(parent) if parent else None)

    def relocate_all(self, movable):
        """Reanida en Outline los ficheros que cambiaron de carpeta.

        Las bases se leen todas antes de escribir ninguna, porque dos ficheros que
        intercambian sus rutas se pisarían la base el uno al otro.
        """
        bases = shadow(self.root / STATE_DIR / "base")
        for relative, document_id, was_at in movable:
            if self.relocate(relative, document_id, was_at, bases):
                self.settle_path(document_id, relative, was_at, bases)

    def relocate(self, relative, document_id, was_at, bases):
        """Reanida en Outline una página cuyo fichero cambió de carpeta.

        Devuelve si la ruta nueva se puede dar por buena, que es lo que deja apuntarla.
        """
        if was_at == relative:
            return True
        collection, parent, nest = self.nesting(relative)
        if collection is None:
            self.unmoved.append(f"wiki/{relative}, fuera de ninguna colección")
            return False
        if parent is not None and nest is None:
            self.unmoved.append(
                f"wiki/{relative}, que colgaría de wiki/{parent} y esa página no existe"
            )
            return False
        before, _, previous = self.nesting(was_at)
        if before is not None and before["id"] == collection["id"] and previous == nest:
            return True
        entry = self.manifest[document_id]
        remote = fetch_document(self.client, document_id)
        if remote is None:
            self.unmoved.append(f"wiki/{relative}, sin contenido accesible en Outline")
            return False
        remote_body = page_body(remote["title"], remote.get("text"))
        overtaken = advanced_by_revision(remote, entry, bases.get(was_at), remote_body)
        payload = {
            "id": document_id,
            "collectionId": collection["id"],
            "index": level_size(relative, self.mirror),
        }
        if nest:
            payload["parentDocumentId"] = nest
        answer = (self.client.post("documents.move", payload) or {}).get("data") or {}
        moved = next(
            (d for d in answer.get("documents") or [] if d["id"] == document_id), None
        )
        if moved is None:
            self.unmoved.append(f"wiki/{relative}, sin respuesta de Outline al moverla")
            return False
        # Mover sube la revisión aunque el texto no cambie, y quedarse con la de antes daría un
        # conflicto que no existe. Pero con Outline por delante la nueva taparía lo que no he
        # visto, así que ahí se queda la vieja y lo arregla el pull siguiente.
        if not overtaken:
            entry["revision"] = moved.get("revision")
        entry["collection"] = collection["name"]
        entry["collectionId"] = collection["id"]
        destino = self.overtaken if overtaken else self.moved
        destino.append(f"wiki/{relative}, desde wiki/{was_at}")
        return True

    def settle_path(self, document_id, relative, was_at, bases):
        """Muda la base con el fichero y apunta en el manifiesto dónde vive ahora."""
        if was_at == relative:
            return
        base = bases.get(was_at)
        if base is not None:
            write(self.root / STATE_DIR / "base" / relative, base)
            if was_at not in self.mirror.bodies:
                shadow_dir = self.root / STATE_DIR / "base"
                discard(shadow_dir / was_at, shadow_dir)
        self.manifest[document_id]["path"] = relative

    def remove(self, doomed, confirmed, complete):
        """Borra las páginas que estaban en el manifiesto del último pull y ya no en el disco."""
        pending = []
        for document_id, entry in doomed:
            folder = f"{entry['path'].removesuffix('.md')}/"
            if any(other.startswith(folder) for other in self.mirror.bodies):
                self.spared.append(
                    f"wiki/{entry['path']}, que todavía tiene páginas colgando en el disco"
                )
            else:
                pending.append((document_id, entry))
        if not pending:
            return
        pages = [f"wiki/{entry['path']}" for _, entry in pending]
        announce(pages)
        if not complete:
            self.spared.extend(pages)
            self.note = (
                "no se borra nada porque el último pull no terminó. Un fichero que falta puede "
                "ser un fallo de red y no un borrado tuyo, así que pasa un 'outline pull'."
            )
            return
        answer = True if confirmed else agree()
        if answer is None:
            self.spared.extend(pages)
        elif not answer:
            self.declined.extend(pages)
        else:
            self.trash(pending)

    def trash(self, pending):
        """Manda las páginas a la papelera y las quita del manifiesto y de la base."""
        condemned = {document_id for document_id, _ in pending}
        shadow_dir = self.root / STATE_DIR / "base"
        for document_id, entry in pending:
            parent = parent_path(entry["path"], self.scope)
            # Outline se lleva a la papelera el árbol entero, así que con la madre basta.
            if parent is None or self.pages_by_path.get(parent) not in condemned:
                self.client.post("documents.delete", {"id": document_id, "permanent": False})
            del self.manifest[document_id]
            discard(shadow_dir / entry["path"], shadow_dir)
            self.deleted.append(f"wiki/{entry['path']}")

    def tell(self):
        """Cuenta cómo fue la pasada y devuelve el código de salida."""
        counted = [(tally, getattr(self, tally.field)) for tally in TALLIES]
        partes = [plural(len(p), tally.one, tally.many) for tally, p in counted if p]
        print(f"{', '.join(partes) or 'nada que subir'} en {self.scope.label()}")
        for tally, pages in counted:
            if tally.heading:
                section(tally.heading, pages)
        if self.note:
            print(self.note)
        if self.clashed:
            print(
                "mira los dos diffs con 'outline diff', deja el fichero como quieras "
                "y márcalo con 'outline resolve'."
            )
        return 1 if any(pages for tally, pages in counted if tally.failure) else 0


def push(client, root, todas, confirmed=False):
    wiki = root / "wiki"
    state = load_state(root)
    complete = state.get("pullComplete", False)
    manifest = state["documents"]
    mirror = Mirror.read(wiki)
    by_path = {entry["path"]: document_id for document_id, entry in manifest.items()}
    claimed = set(mirror.ids.values())

    def identity(relative):
        """De quién es un fichero: de su frontmatter, o de quien el manifiesto puso en su ruta.

        Una ruta cuya página se mudó ya no presta identidad, porque la reclama el frontmatter
        de otro fichero. Lo que ocupa el hueco es una página nueva.
        """
        inherited = by_path.get(relative)
        return mirror.ids.get(relative) or (None if inherited in claimed else inherited)

    identity_of = {relative: identity(relative) for relative in mirror.bodies}
    refuse_duplicates(identity_of)

    if not complete:
        print(
            "aviso: el último pull no terminó del todo, así que la base puede estar a medias. "
            "Pasa un 'outline pull' antes de fiarte de lo que salga aquí."
        )
        print()

    scope = Scope.only_collections(client, root, todas)
    pusher = Pusher(
        client=client,
        root=root,
        mirror=mirror,
        shadowed=shadow(root / STATE_DIR / "base"),
        scope=scope,
        manifest=manifest,
        pages_by_path={**by_path, **mirror.ids},
    )

    # Borrada es la que estaba en el manifiesto del último pull y ya no está en mi disco,
    # nunca la que está en Outline y no en mi disco. Se apunta antes de crear nada.
    alive = {document_id for document_id in identity_of.values() if document_id}
    doomed = [
        (document_id, dict(entry))
        for document_id, entry in sorted(manifest.items(), key=lambda pair: pair[1]["path"])
        if document_id not in alive and scope.covers(entry)
    ]

    newborn, movable = [], []
    for relative in sorted(mirror.bodies):
        document_id = identity_of[relative]
        if document_id is None:
            newborn.append(relative)
            continue
        entry = manifest.get(document_id)
        if entry is None or scope.covers(entry):
            was_at = entry["path"] if entry else relative
            if pusher.update(relative, document_id, entry) and entry is not None:
                movable.append((relative, document_id, was_at))

    # De fuera hacia dentro, para que una hija encuentre a su madre recién creada.
    newborn.sort(key=lambda relative: (relative.count("/"), relative))
    for relative in newborn:
        pusher.create(relative)

    # Después de crear, para que una página pueda mudarse debajo de otra recién nacida.
    pusher.relocate_all(movable)

    pusher.remove(doomed, confirmed, complete)
    write_manifest(root, manifest, complete=complete)
    return pusher.tell()


def show_diff(heading, base, other, name):
    print(heading)
    body = "".join(
        difflib.unified_diff(
            (base or "").splitlines(True),
            (other or "").splitlines(True),
            "base",
            name,
        )
    )
    print(body.rstrip("\n") if body else "  sin cambios")
    print()


def diff(client, root, needles):
    manifest = load_manifest(root)
    mirror = Mirror.read(root / "wiki")
    shadowed = shadow(root / STATE_DIR / "base")
    lives = located(mirror, manifest)

    if needles:
        targets = [find_page(root, manifest, needle, lives) for needle in needles]
    else:
        targets = [
            document_id
            for document_id, entry in manifest.items()
            if mirror.bodies.get(lives[document_id])
            not in (None, shadowed.get(entry["path"]))
        ]

    shown = 0
    for document_id in targets:
        entry = manifest[document_id]
        local = mirror.bodies.get(lives[document_id])
        base = shadowed.get(entry["path"])
        remote = fetch_document(client, document_id)
        remote_body = page_body(remote["title"], remote.get("text")) if remote else None
        theirs = (
            remote is None
            or base is None
            or advanced_by_revision(remote, entry, base, remote_body)
        )
        if not needles and not (local != base and theirs):
            continue
        shown += 1
        print(f"wiki/{lives[document_id]}\n")
        show_diff("base -> local, lo que has escrito tú", base, local, "local")
        show_diff("base -> remoto, lo que hay en Outline", base, remote_body, "remoto")
    if not needles and not shown:
        print("sin conflictos")
    return 0


def resolve(client, root, needles):
    """Da un conflicto por resuelto adelantando la base al remoto de ahora mismo."""
    if not needles:
        fail(
            "dime qué página doy por resuelta, por id, ruta o título. "
            "Las que están en conflicto salen en 'outline status'."
        )
    state = load_state(root)
    manifest = state["documents"]
    mirror = Mirror.read(root / "wiki")
    known = {**{i: {"path": r, "revision": None} for r, i in mirror.ids.items()}, **manifest}
    lives = located(mirror, known)

    for needle in needles:
        document_id = find_page(root, known, needle, lives)
        entry = dict(known[document_id])
        remote = fetch_document(client, document_id)
        if remote is None:
            fail(f"Outline no devuelve contenido para wiki/{lives[document_id]}")
        # La base va a la ruta del manifiesto, que es contra la que compara el push.
        write(
            root / STATE_DIR / "base" / entry["path"],
            page_body(remote["title"], remote.get("text")),
        )
        entry["revision"] = remote.get("revision")
        entry.setdefault("collectionId", remote.get("collectionId"))
        manifest[document_id] = entry
        print(
            f"wiki/{lives[document_id]}: la base pasa a la revisión {remote.get('revision')} "
            "de Outline. Lo que tengas en local se sube con 'outline push'."
        )
    write_manifest(root, manifest, complete=state.get("pullComplete", False))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="outline", description="Espejo local de una wiki de Outline"
    )
    parser.add_argument(
        "command", nargs="?", default="pull",
        choices=["pull", "push", "status", "diff", "resolve", "check"],
        help="pull baja la wiki, push sube lo que has escrito, status dice en qué estado "
             "estás, diff enseña los dos lados de un conflicto, resolve lo da por resuelto, "
             "check compara revisiones sin escribir nada",
    )
    parser.add_argument(
        "targets", nargs="*",
        help="páginas para check, diff o resolve, por id, ruta o título",
    )
    parser.add_argument(
        "--all", dest="todas", action="store_true",
        help="trabaja con todas las colecciones en vez de la que se llama como el proyecto",
    )
    parser.add_argument(
        "--yes", dest="confirmado", action="store_true",
        help="confirma el borrado de las páginas que ya no están en el disco",
    )
    parser.add_argument("--url", help="dirección de la instancia. Por defecto, OUTLINE_URL")
    arguments = parser.parse_args(argv)

    client = Outline(read_url(arguments.url), read_token())
    root = project_root()
    if arguments.command == "check":
        return check(client, root, arguments.targets)
    if arguments.command == "status":
        return status(client, root, arguments.todas)
    if arguments.command == "push":
        return push(client, root, arguments.todas, arguments.confirmado)
    if arguments.command == "diff":
        return diff(client, root, arguments.targets)
    if arguments.command == "resolve":
        return resolve(client, root, arguments.targets)
    return pull(client, root, arguments.todas)


if __name__ == "__main__":
    sys.exit(main())
