"""Tests de outline.py.

La única costura es el transporte HTTP: el método que hace el POST contra la API se
sustituye por un doble que sirve un árbol de wiki guionizado y registra las peticiones.
La raíz del proyecto es un directorio temporal. Todo lo demás pasa por la línea de
comandos de verdad.
"""

import contextlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import outline

ARBOL = {
    "Taller": [
        {
            "title": "Diagnóstico",
            "text": "Cuerpo del diagnóstico.",
            "children": [{"title": "Auditoría de ruido", "text": "Cuerpo de la auditoría."}],
        },
        {"title": "Albarán", "text": "Cuerpo del albarán."},
    ],
    "Almacén Ñuble": [{"title": "Inventario", "text": "Las existencias."}],
}

ESCRITURAS = {"documents.create", "documents.update", "documents.move", "documents.delete"}


class Transporte:
    """Doble del POST: sirve el árbol guionizado y registra lo que se le pide."""

    def __init__(self, arbol=ARBOL):
        self.peticiones = []
        self.colecciones = []
        self.documentos = {}
        self.arboles = {}
        self.romper = None
        self.inaccesibles = set()
        self.papelera = []
        self.destruidos = []
        self.mudos = set()
        self.creados = 0
        for indice, (nombre, nodos) in enumerate(arbol.items(), start=1):
            coleccion = f"col-{indice}"
            self.colecciones.append({"id": coleccion, "name": nombre})
            self.arboles[coleccion] = self._construir(nodos, coleccion)

    def _construir(self, nodos, coleccion, padre=None):
        salida = []
        for nodo in nodos:
            documento_id = self._alta(nodo["title"], nodo["text"], coleccion, padre)
            salida.append(
                {
                    "id": documento_id,
                    "title": nodo["title"],
                    "children": self._construir(
                        nodo.get("children", []), coleccion, documento_id
                    ),
                }
            )
        return salida

    def _alta(self, titulo, texto, coleccion, padre=None):
        self.creados += 1
        documento_id = f"doc-{self.creados:03d}"
        self.documentos[documento_id] = {
            "id": documento_id,
            "title": titulo,
            "text": texto,
            "revision": 1,
            "updatedAt": "2026-09-07T00:00:00.000Z",
            "urlId": documento_id,
            "url": f"/doc/{documento_id}",
            "collectionId": coleccion,
            "parentDocumentId": padre,
        }
        return documento_id

    def post(self, endpoint, payload):
        self.peticiones.append((endpoint, payload))
        if endpoint == self.romper:
            raise ConnectionError("la red se cayó a media pasada")
        if endpoint == "collections.list":
            return self._pagina(self.colecciones)
        if endpoint == "documents.list":
            coleccion = payload.get("collectionId")
            return self._pagina(
                [
                    dict(d) for d in self.documentos.values()
                    if d["collectionId"] == coleccion and d["id"] not in self.inaccesibles
                ]
            )
        if endpoint == "collections.documents":
            return {"data": self.arboles[payload["id"]]}
        if endpoint == "documents.info":
            if payload["id"] in self.inaccesibles:
                return {"data": None}
            return {"data": dict(self.documentos[payload["id"]])}
        if endpoint == "documents.update":
            documento = self.documentos[payload["id"]]
            documento["text"] = payload["text"]
            documento["revision"] += 1
            if payload.get("title"):
                self.retitular(payload["id"], payload["title"])
            return {"data": dict(documento)}
        if endpoint == "documents.create":
            padre = payload.get("parentDocumentId")
            coleccion = payload.get("collectionId") or self.documentos[padre]["collectionId"]
            documento_id = self._alta(payload["title"], payload["text"], coleccion, padre)
            nodo = {"id": documento_id, "title": payload["title"], "children": []}
            nivel = self.arboles[coleccion]
            if padre:
                nivel = self._buscar(nivel, padre)["children"]
            nivel.insert(0, nodo)
            return {"data": dict(self.documentos[documento_id])}
        if endpoint == "documents.move":
            documento = self.documentos[payload["id"]]
            nodo = self._buscar(self.arboles[documento["collectionId"]], payload["id"])
            for coleccion, arbol in self.arboles.items():
                self.arboles[coleccion] = self._podar(arbol, payload["id"])
            coleccion = payload.get("collectionId") or documento["collectionId"]
            padre = payload.get("parentDocumentId")
            nivel = self.arboles[coleccion]
            if padre:
                nivel = self._buscar(nivel, padre)["children"]
            nivel.insert(min(payload.get("index", 0), len(nivel)), nodo)
            documento["collectionId"] = coleccion
            documento["parentDocumentId"] = padre
            documento["revision"] += 1
            if payload["id"] in self.mudos:
                return {"data": {"documents": [], "collections": []}}
            return {"data": {"documents": [dict(documento)], "collections": []}}
        if endpoint == "documents.delete":
            documento = self.documentos[payload["id"]]
            nodo = self._buscar(self.arboles[documento["collectionId"]], payload["id"])
            destino = self.destruidos if payload.get("permanent") else self.papelera
            for descendiente in self._rama(nodo):
                destino.append(descendiente)
                del self.documentos[descendiente]
            for coleccion, arbol in self.arboles.items():
                self.arboles[coleccion] = self._podar(arbol, payload["id"])
            return {"data": None}
        raise AssertionError(f"endpoint no guionizado: {endpoint}")

    def _rama(self, nodo):
        """El nodo y su descendencia, que es lo que Outline se lleva de una sola vez."""
        yield nodo["id"]
        for hijo in nodo["children"]:
            yield from self._rama(hijo)

    def _buscar(self, nodos, documento_id):
        for nodo in nodos:
            if nodo["id"] == documento_id:
                return nodo
            encontrado = self._buscar(nodo["children"], documento_id)
            if encontrado:
                return encontrado
        return None

    def editar(self, documento_id, texto):
        """Un socio edita esa página en Outline, lo que adelanta su revisión."""
        documento = self.documentos[documento_id]
        documento["text"] = texto
        documento["revision"] += 1

    def retitular(self, documento_id, titulo):
        self.documentos[documento_id]["title"] = titulo
        for coleccion, arbol in self.arboles.items():
            self.arboles[coleccion] = self._retitular(arbol, documento_id, titulo)

    def _retitular(self, nodos, documento_id, titulo):
        return [
            {
                **nodo,
                "title": titulo if nodo["id"] == documento_id else nodo["title"],
                "children": self._retitular(nodo["children"], documento_id, titulo),
            }
            for nodo in nodos
        ]

    def escrituras(self):
        """Las peticiones que cambian algo en Outline, en el orden en que se hicieron."""
        return [(e, p) for e, p in self.peticiones if e in ESCRITURAS]

    def crear(self, titulo, texto, coleccion="col-1", padre=None):
        """Una socia crea una página en Outline después de mi último pull."""
        documento_id = self._alta(titulo, texto, coleccion, padre)
        nivel = self.arboles[coleccion]
        if padre:
            nivel = self._buscar(nivel, padre)["children"]
        nivel.append({"id": documento_id, "title": titulo, "children": []})
        return documento_id

    def borrar(self, documento_id):
        del self.documentos[documento_id]
        for coleccion, arbol in self.arboles.items():
            self.arboles[coleccion] = self._podar(arbol, documento_id)

    def _podar(self, nodos, documento_id):
        return [
            {**nodo, "children": self._podar(nodo["children"], documento_id)}
            for nodo in nodos
            if nodo["id"] != documento_id
        ]

    def colecciones_pedidas(self):
        return [p["collectionId"] for e, p in self.peticiones if e == "documents.list"]

    @staticmethod
    def _pagina(items):
        return {"data": items, "pagination": {"total": len(items)}}


class Teclado(io.StringIO):
    """Un stdin con alguien delante, que es lo que hace que el push llegue a preguntar."""

    def isatty(self):
        return True


class Caso(unittest.TestCase):
    def setUp(self):
        self.transporte = Transporte()
        original = outline.Outline.post
        outline.Outline.post = lambda _yo, endpoint, payload: self.transporte.post(
            endpoint, payload
        )
        self.addCleanup(setattr, outline.Outline, "post", original)

        temporal = tempfile.TemporaryDirectory()
        # El chdir de vuelta se registra después para que corra antes del borrado:
        # en Windows no se puede borrar un directorio que un proceso tenga como cwd.
        self.addCleanup(temporal.cleanup)
        self.addCleanup(os.chdir, Path.cwd())
        self.temporal = Path(temporal.name)

        os.environ["OUTLINE_URL"] = "https://wiki.ejemplo.com"
        os.environ["OUTLINE_API_TOKEN"] = "token-de-prueba"

        # Sin nadie al teclado por defecto, para que ninguna pregunta cuelgue la suite.
        self.addCleanup(setattr, sys, "stdin", sys.stdin)
        sys.stdin = io.StringIO()

    def teclear(self, respuesta):
        """Lo que la persona contesta cuando el push pide confirmación."""
        sys.stdin = Teclado(respuesta + "\n")

    def proyecto(self, nombre):
        raiz = self.temporal / nombre
        (raiz / ".git").mkdir(parents=True)
        os.chdir(raiz)
        return raiz

    def ejecutar(self, *argv):
        with contextlib.redirect_stdout(io.StringIO()):
            return outline.main(list(argv))

    def salida(self, *argv):
        escrito = io.StringIO()
        with contextlib.redirect_stdout(escrito):
            outline.main(list(argv))
        return escrito.getvalue()

    def ejecutar_fallando(self, *argv):
        error = io.StringIO()
        with contextlib.redirect_stderr(error), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as salida:
                outline.main(list(argv))
        self.assertEqual(salida.exception.code, 1)
        return error.getvalue()

    def estado(self, raiz):
        return json.loads((raiz / ".outline" / "manifest.json").read_text(encoding="utf-8"))

    def identificador(self, raiz, ruta):
        documentos = self.estado(raiz)["documents"]
        return next(i for i, entrada in documentos.items() if entrada["path"] == ruta)

    def pagina(self, raiz, ruta):
        return (raiz / "wiki" / ruta).read_text(encoding="utf-8")

    def base(self, raiz, ruta):
        return (raiz / ".outline" / "base" / ruta).read_text(encoding="utf-8")

    def editar_local(self, raiz, ruta, linea):
        """Edita la página como lo haría la persona. Devuelve el texto que queda en el disco."""
        pagina = raiz / "wiki" / ruta
        texto = pagina.read_text(encoding="utf-8").rstrip("\n") + f"\n\n{linea}\n"
        pagina.write_text(texto, encoding="utf-8")
        return texto

    def crear_local(self, raiz, ruta, texto):
        """Un fichero que escribí yo y que Outline no conoce todavía."""
        destino = raiz / "wiki" / ruta
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(texto, encoding="utf-8")
        return texto

    def retitular(self, raiz, ruta, titulo):
        """Renombra en Outline la página que hoy vive en esa ruta. Devuelve su identificador."""
        identificador = self.identificador(raiz, ruta)
        self.transporte.retitular(identificador, titulo)
        return identificador

    def olvidar(self, raiz, ruta):
        """Deja la página solo con la identidad de su frontmatter, sin base ni manifiesto."""
        identificador = self.identificador(raiz, ruta)
        (raiz / ".outline" / "base" / ruta).unlink()
        estado = self.estado(raiz)
        del estado["documents"][identificador]
        (raiz / ".outline" / "manifest.json").write_text(
            json.dumps(estado, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return identificador

    def identificador_remoto(self, titulo):
        return next(d["id"] for d in self.transporte.documentos.values() if d["title"] == titulo)

    def mover_fichero(self, raiz, origen, destino):
        """Arrastra el fichero de la página a otra carpeta, sin tocar lo que hay dentro."""
        salida = raiz / "wiki" / destino
        salida.parent.mkdir(parents=True, exist_ok=True)
        (raiz / "wiki" / origen).rename(salida)

    def montar_conflicto(self):
        """Una página cambiada aquí y en Outline desde el último pull."""
        raiz = self.proyecto("Taller")
        self.ejecutar("pull")
        self.editar_local(raiz, "diagnostico.md", "Lo que escribí yo.")
        self.transporte.editar(
            self.identificador(raiz, "diagnostico.md"),
            "Cuerpo del diagnóstico.\n\nLo que escribió mi socia.",
        )
        return raiz

    def degradar_manifiesto(self, raiz):
        """El manifiesto como lo escribía la versión anterior, sin el id de la colección."""
        estado = self.estado(raiz)
        for entrada in estado["documents"].values():
            entrada.pop("collectionId", None)
        (raiz / ".outline" / "manifest.json").write_text(
            json.dumps(estado, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )


class Ambito(Caso):
    def test_baja_solo_la_coleccion_que_se_llama_como_el_proyecto(self):
        raiz = self.proyecto("Taller")
        self.ejecutar("pull")

        self.assertTrue((raiz / "wiki" / "diagnostico.md").is_file())
        self.assertFalse((raiz / "wiki" / "inventario.md").exists())
        self.assertEqual(self.transporte.colecciones_pedidas(), ["col-1"])

    def test_la_coincidencia_ignora_mayusculas(self):
        raiz = self.proyecto("taller")
        self.ejecutar("pull")

        self.assertTrue((raiz / "wiki" / "diagnostico.md").is_file())

    def test_la_coincidencia_ignora_acentos(self):
        raiz = self.proyecto("almacen-nuble")
        self.ejecutar("pull")

        self.assertTrue((raiz / "wiki" / "inventario.md").is_file())
        self.assertEqual(self.transporte.colecciones_pedidas(), ["col-2"])

    def test_sin_coincidencia_falla_y_lista_las_colecciones(self):
        raiz = self.proyecto("OtroProyecto")
        mensaje = self.ejecutar_fallando("pull")

        self.assertIn("Taller", mensaje)
        self.assertIn("Almacén Ñuble", mensaje)
        self.assertIn("--all", mensaje)
        self.assertFalse((raiz / "wiki").exists())

    def test_all_baja_todas_las_colecciones(self):
        raiz = self.proyecto("Cualquiera")
        self.ejecutar("pull", "--all")

        self.assertTrue((raiz / "wiki" / "taller" / "diagnostico.md").is_file())
        self.assertTrue((raiz / "wiki" / "almacen-nuble" / "inventario.md").is_file())


class Rutas(Caso):
    def test_con_una_coleccion_las_paginas_cuelgan_de_wiki(self):
        raiz = self.proyecto("Taller")
        self.ejecutar("pull")

        self.assertFalse((raiz / "wiki" / "taller").exists())
        self.assertTrue((raiz / "wiki" / "diagnostico" / "auditoria-de-ruido.md").is_file())

    def test_con_all_el_primer_segmento_es_la_coleccion(self):
        raiz = self.proyecto("Cualquiera")
        self.ejecutar("pull", "--all")

        self.assertTrue(
            (raiz / "wiki" / "taller" / "diagnostico" / "auditoria-de-ruido.md").is_file()
        )


class Pagina(Caso):
    def partes(self, raiz, ruta):
        """El frontmatter y el cuerpo de una página, separados por su cierre."""
        vacio, campos, cuerpo = (raiz / "wiki" / ruta).read_text(encoding="utf-8").split("---\n", 2)
        self.assertEqual(vacio, "")
        return campos, cuerpo

    def test_el_frontmatter_lleva_el_outline_id_y_nada_mas(self):
        raiz = self.proyecto("Taller")
        self.ejecutar("pull")

        campos, _ = self.partes(raiz, "diagnostico.md")
        identificador = self.identificador(raiz, "diagnostico.md")
        self.assertEqual(campos.strip().splitlines(), [f"outline_id: {identificador}"])

    def test_el_titulo_es_el_encabezado_de_nivel_1_del_cuerpo(self):
        raiz = self.proyecto("Taller")
        self.ejecutar("pull")

        _, cuerpo = self.partes(raiz, "diagnostico.md")
        self.assertEqual(cuerpo, "\n# Diagnóstico\n\nCuerpo del diagnóstico.\n")

    def test_sin_encabezado_el_titulo_no_sale_de_un_bloque_de_codigo(self):
        raiz = self.proyecto("Taller")
        self.ejecutar("pull")

        identificador = self.identificador(raiz, "diagnostico.md")
        (raiz / "wiki" / "diagnostico.md").write_text(
            f"---\noutline_id: {identificador}\n---\n\n```bash\n# borrar el ruido\n```\n",
            encoding="utf-8",
        )

        self.assertIn("no coincide", self.ejecutar_fallando("check", "borrar el ruido"))

    def test_no_se_escribe_la_cabecera_de_comentario_html(self):
        raiz = self.proyecto("Taller")
        self.ejecutar("pull")

        texto = (raiz / "wiki" / "diagnostico.md").read_text(encoding="utf-8")
        self.assertNotIn("<!--", texto)
        self.assertNotIn("No editar a mano", texto)

    def test_en_wiki_no_queda_nada_que_no_sea_una_pagina(self):
        raiz = self.proyecto("Taller")
        self.ejecutar("pull")

        wiki = raiz / "wiki"
        sueltos = [
            f.relative_to(wiki).as_posix() for f in wiki.rglob("*") if f.is_file() and f.suffix != ".md"
        ]
        self.assertEqual(sueltos, [])
        self.assertFalse((wiki / "INDEX.md").exists())


class Manifiesto(Caso):
    def test_guarda_por_pagina_la_ruta_la_revision_y_la_coleccion(self):
        raiz = self.proyecto("Taller")
        self.ejecutar("pull")

        documentos = self.estado(raiz)["documents"]
        identificador = self.identificador(raiz, "diagnostico/auditoria-de-ruido.md")
        self.assertEqual(
            documentos[identificador],
            {
                "path": "diagnostico/auditoria-de-ruido.md",
                "revision": 1,
                "collection": "Taller",
                "collectionId": "col-1",
            },
        )

    def test_la_coleccion_se_sigue_por_su_id_y_no_por_su_nombre(self):
        raiz = self.proyecto("Taller")
        self.ejecutar("pull")
        self.transporte.colecciones[0]["name"] = "Taller mecánico"

        self.ejecutar("pull", "--all")

        self.assertFalse((raiz / "wiki" / "diagnostico.md").exists())
        self.assertTrue((raiz / "wiki" / "taller-mecanico" / "diagnostico.md").is_file())

    def test_un_pull_que_termina_deja_la_marca_de_completo(self):
        raiz = self.proyecto("Taller")
        self.ejecutar("pull")

        self.assertTrue(self.estado(raiz)["pullComplete"])

    def test_un_pull_interrumpido_no_deja_la_marca_y_conserva_las_rutas(self):
        raiz = self.proyecto("Taller")
        self.ejecutar("pull")
        anteriores = self.estado(raiz)["documents"]

        self.transporte.romper = "collections.documents"
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(ConnectionError):
                outline.main(["pull"])

        self.assertFalse(self.estado(raiz)["pullComplete"])
        self.assertEqual(self.estado(raiz)["documents"], anteriores)


class Base(Caso):
    def test_se_guarda_sin_frontmatter(self):
        raiz = self.proyecto("Taller")
        self.ejecutar("pull")

        base = (raiz / ".outline" / "base" / "diagnostico.md").read_text(encoding="utf-8")
        self.assertNotIn("outline_id", base)
        self.assertEqual(base, "# Diagnóstico\n\nCuerpo del diagnóstico.\n")

    def test_es_la_pagina_local_menos_su_frontmatter(self):
        raiz = self.proyecto("Taller")
        self.ejecutar("pull")

        for pagina in (raiz / "wiki").rglob("*.md"):
            relativa = pagina.relative_to(raiz / "wiki")
            local = pagina.read_text(encoding="utf-8")
            base = (raiz / ".outline" / "base" / relativa).read_text(encoding="utf-8")
            identificador = self.identificador(raiz, relativa.as_posix())
            self.assertEqual(local, f"---\noutline_id: {identificador}\n---\n\n{base}")

    def test_reproduce_la_estructura_de_carpetas_de_wiki(self):
        raiz = self.proyecto("Cualquiera")
        self.ejecutar("pull", "--all")

        wiki = sorted(p.relative_to(raiz / "wiki").as_posix() for p in (raiz / "wiki").rglob("*.md"))
        base = sorted(
            p.relative_to(raiz / ".outline" / "base").as_posix()
            for p in (raiz / ".outline" / "base").rglob("*.md")
        )
        self.assertEqual(base, wiki)
        self.assertIn("taller/diagnostico/auditoria-de-ruido.md", base)


class Indice(Caso):
    def indice(self, raiz):
        return (raiz / ".outline" / "index.md").read_text(encoding="utf-8")

    def test_trae_el_arbol_en_el_orden_de_la_barra_lateral(self):
        raiz = self.proyecto("Taller")
        self.ejecutar("pull")

        lineas = [l for l in self.indice(raiz).splitlines() if l.lstrip().startswith("- [")]
        self.assertEqual(
            [re.search(r"\[(.+?)\]", l).group(1) for l in lineas],
            ["Diagnóstico", "Auditoría de ruido", "Albarán"],
        )
        self.assertTrue(lineas[1].startswith("  - ["))

    def test_lista_las_colecciones_y_sus_paginas_con_el_id(self):
        raiz = self.proyecto("Cualquiera")
        self.ejecutar("pull", "--all")

        indice = self.indice(raiz)
        self.assertIn("## Taller", indice)
        self.assertIn("## Almacén Ñuble", indice)
        self.assertIn(f"`{self.identificador(raiz, 'taller/albaran.md')}`", indice)

    def test_sus_enlaces_llegan_a_las_paginas(self):
        raiz = self.proyecto("Taller")
        self.ejecutar("pull")

        enlaces = re.findall(r"\]\((.+?)\)", self.indice(raiz))
        self.assertEqual(len(enlaces), 3)
        for enlace in enlaces:
            self.assertTrue((raiz / ".outline" / enlace).is_file(), enlace)


class Check(Caso):
    def test_con_la_copia_al_dia_no_senala_nada(self):
        self.proyecto("Taller")
        self.ejecutar("pull")

        self.assertEqual(self.ejecutar("check"), 0)

    def test_una_revision_nueva_en_remoto_sale_como_desactualizada(self):
        raiz = self.proyecto("Taller")
        self.ejecutar("pull")
        self.transporte.documentos[self.identificador(raiz, "diagnostico.md")]["revision"] = 2

        self.assertEqual(self.ejecutar("check", "Diagnóstico"), 1)

    def test_una_pagina_movida_se_comprueba_por_su_titulo(self):
        raiz = self.proyecto("Taller")
        self.ejecutar("pull")
        self.transporte.documentos[self.identificador(raiz, "albaran.md")]["revision"] = 2
        self.mover_fichero(raiz, "albaran.md", "diagnostico/albaran.md")

        salida = self.salida("check", "Albarán")

        self.assertIn("DESACTUALIZADO Albarán", salida)

    def test_sin_manifiesto_pide_un_pull(self):
        self.proyecto("Taller")

        self.assertIn("outline pull", self.ejecutar_fallando("check"))


class TresVersiones(Caso):
    """Las cuatro combinaciones de la tabla: local contra base, y remoto contra base."""

    def preparar(self):
        raiz = self.proyecto("Taller")
        self.ejecutar("pull")
        return raiz

    def test_limpia_el_pull_la_deja_como_estaba(self):
        raiz = self.preparar()
        antes = self.pagina(raiz, "diagnostico.md")

        self.ejecutar("pull")

        self.assertEqual(self.pagina(raiz, "diagnostico.md"), antes)
        self.assertEqual(self.base(raiz, "diagnostico.md"), "# Diagnóstico\n\nCuerpo del diagnóstico.\n")

    def test_remota_adelantada_se_actualiza_en_local_y_en_la_base(self):
        raiz = self.preparar()
        identificador = self.identificador(raiz, "diagnostico.md")
        self.transporte.editar(identificador, "Lo que escribió mi socio.")

        self.ejecutar("pull")

        self.assertIn("Lo que escribió mi socio.", self.pagina(raiz, "diagnostico.md"))
        self.assertIn("Lo que escribió mi socio.", self.base(raiz, "diagnostico.md"))
        self.assertEqual(self.estado(raiz)["documents"][identificador]["revision"], 2)

    def test_sucia_el_pull_no_la_toca(self):
        raiz = self.preparar()
        mio = self.editar_local(raiz, "diagnostico.md", "Lo que escribí yo.")

        self.ejecutar("pull")

        self.assertEqual(self.pagina(raiz, "diagnostico.md"), mio)
        self.assertNotIn("Lo que escribí yo.", self.base(raiz, "diagnostico.md"))

    def test_en_conflicto_el_pull_no_la_toca_y_la_base_no_avanza(self):
        raiz = self.preparar()
        identificador = self.identificador(raiz, "diagnostico.md")
        mio = self.editar_local(raiz, "diagnostico.md", "Lo que escribí yo.")
        self.transporte.editar(identificador, "Lo que escribió mi socio.")

        self.ejecutar("pull")

        self.assertEqual(self.pagina(raiz, "diagnostico.md"), mio)
        self.assertNotIn("mi socio", self.base(raiz, "diagnostico.md"))
        self.assertEqual(self.estado(raiz)["documents"][identificador]["revision"], 1)

    def test_una_pagina_sucia_no_estorba_a_las_demas(self):
        raiz = self.preparar()
        self.editar_local(raiz, "diagnostico.md", "Lo que escribí yo.")
        self.transporte.editar(self.identificador(raiz, "albaran.md"), "Albarán al día.")

        self.ejecutar("pull")

        self.assertIn("Albarán al día.", self.pagina(raiz, "albaran.md"))


class PullNoDestructivo(Caso):
    def test_no_borra_la_carpeta_wiki(self):
        raiz = self.proyecto("Taller")
        self.ejecutar("pull")
        mias = raiz / "wiki" / "mis-notas.md"
        mias.write_text("# Mis notas\n\nTodavía sin subir.\n", encoding="utf-8")

        self.ejecutar("pull")

        self.assertEqual(mias.read_text(encoding="utf-8"), "# Mis notas\n\nTodavía sin subir.\n")

    def test_un_pull_interrumpido_conserva_las_paginas_ya_bajadas(self):
        raiz = self.proyecto("Taller")
        self.ejecutar("pull")
        self.transporte.romper = "collections.documents"

        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(ConnectionError):
                outline.main(["pull"])

        self.assertTrue((raiz / "wiki" / "diagnostico.md").is_file())
        self.assertFalse(self.estado(raiz)["pullComplete"])

    def test_una_pagina_sin_contenido_accesible_ni_se_borra_ni_deja_la_marca(self):
        raiz = self.proyecto("Taller")
        self.ejecutar("pull")
        identificador = self.identificador(raiz, "albaran.md")
        self.transporte.inaccesibles.add(identificador)

        salida = self.salida("pull")

        self.assertTrue((raiz / "wiki" / "albaran.md").is_file())
        self.assertIn(identificador, self.estado(raiz)["documents"])
        self.assertFalse(self.estado(raiz)["pullComplete"])
        self.assertIn("sin contenido accesible", salida)

    def test_funciona_con_un_proceso_dentro_de_wiki(self):
        raiz = self.proyecto("Taller")
        self.ejecutar("pull")
        self.transporte.editar(self.identificador(raiz, "diagnostico.md"), "Cuerpo nuevo.")
        os.chdir(raiz / "wiki" / "diagnostico")

        self.ejecutar("pull")

        self.assertIn("Cuerpo nuevo.", self.pagina(raiz, "diagnostico.md"))

    def test_una_pagina_que_muevo_de_carpeta_no_vuelve_a_su_sitio(self):
        raiz = self.proyecto("Taller")
        self.ejecutar("pull")
        destino = raiz / "wiki" / "diagnostico" / "albaran.md"
        (raiz / "wiki" / "albaran.md").rename(destino)

        salida = self.salida("pull")

        self.assertTrue(destino.is_file())
        self.assertFalse((raiz / "wiki" / "albaran.md").exists())
        self.assertIn("sin tocar por tener cambios locales (1)", salida)

    def test_una_base_a_medio_escribir_se_cura_en_la_pasada_siguiente(self):
        raiz = self.proyecto("Taller")
        self.ejecutar("pull")
        (raiz / ".outline" / "base" / "albaran.md").write_text("a medias\n", encoding="utf-8")

        salida = self.salida("pull")

        self.assertEqual(self.base(raiz, "albaran.md"), "# Albarán\n\nCuerpo del albarán.\n")
        self.assertNotIn("sin tocar", salida)

    def test_lista_cuantas_paginas_se_salto_y_cuales(self):
        raiz = self.proyecto("Taller")
        self.ejecutar("pull")
        self.editar_local(raiz, "diagnostico.md", "Lo que escribí yo.")

        salida = self.salida("pull")

        self.assertIn("sin tocar por tener cambios locales (1)", salida)
        self.assertIn("wiki/diagnostico.md", salida)

    def test_una_pagina_renombrada_en_outline_se_mueve_en_local(self):
        raiz = self.proyecto("Taller")
        self.ejecutar("pull")
        self.retitular(raiz, "albaran.md", "Albarán de entrega")

        self.ejecutar("pull")

        self.assertTrue((raiz / "wiki" / "albaran-de-entrega.md").is_file())
        self.assertFalse((raiz / "wiki" / "albaran.md").exists())
        self.assertFalse((raiz / ".outline" / "base" / "albaran.md").exists())

    def test_al_mover_una_pagina_no_pisa_un_fichero_mio_en_el_destino(self):
        raiz = self.proyecto("Taller")
        self.ejecutar("pull")
        mio = self.crear_local(raiz, "albaran-de-entrega.md", "Notas que Outline no conoce.\n")
        identificador = self.retitular(raiz, "albaran.md", "Albarán de entrega")

        salida = self.salida("pull")

        self.assertEqual(self.pagina(raiz, "albaran-de-entrega.md"), mio)
        self.assertIn("Cuerpo del albarán.", self.pagina(raiz, "albaran.md"))
        self.assertEqual(self.estado(raiz)["documents"][identificador]["path"], "albaran.md")
        self.assertIn("sin mover porque el destino tiene cambios locales", salida)
        self.assertIn("wiki/albaran-de-entrega.md", salida)

    def test_liberado_el_destino_la_pasada_siguiente_mueve_la_pagina(self):
        raiz = self.proyecto("Taller")
        self.ejecutar("pull")
        self.crear_local(raiz, "albaran-de-entrega.md", "Notas que Outline no conoce.\n")
        self.retitular(raiz, "albaran.md", "Albarán de entrega")
        self.ejecutar("pull")
        (raiz / "wiki" / "albaran-de-entrega.md").unlink()

        self.ejecutar("pull")

        self.assertIn("Cuerpo del albarán.", self.pagina(raiz, "albaran-de-entrega.md"))
        self.assertFalse((raiz / "wiki" / "albaran.md").exists())

    def test_dos_paginas_que_se_intercambian_el_titulo_no_se_pisan(self):
        raiz = self.proyecto("Taller")
        self.ejecutar("pull")
        uno = self.retitular(raiz, "diagnostico.md", "Albarán")
        dos = self.retitular(raiz, "albaran.md", "Diagnóstico")

        self.ejecutar("pull")

        self.assertIn("Cuerpo del diagnóstico.", self.pagina(raiz, "albaran.md"))
        self.assertIn("Cuerpo del albarán.", self.pagina(raiz, "diagnostico.md"))
        self.assertEqual(self.identificador(raiz, "albaran.md"), uno)
        self.assertEqual(self.identificador(raiz, "diagnostico.md"), dos)


class PullBorrados(Caso):
    def test_una_pagina_limpia_borrada_en_outline_se_borra_en_local(self):
        raiz = self.proyecto("Taller")
        self.ejecutar("pull")
        identificador = self.identificador(raiz, "albaran.md")
        self.transporte.borrar(identificador)

        self.ejecutar("pull")

        self.assertFalse((raiz / "wiki" / "albaran.md").exists())
        self.assertFalse((raiz / ".outline" / "base" / "albaran.md").exists())
        self.assertNotIn(identificador, self.estado(raiz)["documents"])

    def test_una_pagina_sucia_borrada_en_outline_se_queda(self):
        raiz = self.proyecto("Taller")
        self.ejecutar("pull")
        identificador = self.identificador(raiz, "albaran.md")
        mio = self.editar_local(raiz, "albaran.md", "Lo que escribí yo.")
        self.transporte.borrar(identificador)

        salida = self.salida("pull")

        self.assertEqual(self.pagina(raiz, "albaran.md"), mio)
        self.assertIn(identificador, self.estado(raiz)["documents"])
        self.assertIn("borrada en Outline", salida)

    def test_con_un_manifiesto_heredado_la_pagina_borrada_se_limpia_igual(self):
        raiz = self.proyecto("Taller")
        self.ejecutar("pull")
        identificador = self.identificador(raiz, "albaran.md")
        self.degradar_manifiesto(raiz)
        self.transporte.borrar(identificador)

        self.ejecutar("pull")

        self.assertFalse((raiz / "wiki" / "albaran.md").exists())
        entradas = self.estado(raiz)["documents"]
        self.assertNotIn(identificador, entradas)
        self.assertTrue(all(entrada["collectionId"] == "col-1" for entrada in entradas.values()))

    def test_una_coleccion_fuera_de_ambito_no_se_borra(self):
        raiz = self.proyecto("Taller")
        self.ejecutar("pull", "--all")

        self.ejecutar("pull")

        self.assertTrue((raiz / "wiki" / "almacen-nuble" / "inventario.md").is_file())
        self.assertTrue((raiz / "wiki" / "diagnostico.md").is_file())
        self.assertFalse((raiz / "wiki" / "taller" / "diagnostico.md").exists())


class Status(Caso):
    def secciones(self, salida):
        """La salida agrupada: cada encabezado con las páginas que lista debajo."""
        grupos, actual = {}, None
        for linea in salida.splitlines():
            if not linea.strip():
                continue
            if linea.startswith("  ") and actual:
                grupos[actual].append(linea.strip())
            elif linea.endswith("):"):
                actual = linea
                grupos[actual] = []
            else:
                actual = None
        return grupos

    def seccion(self, salida, prefijo):
        for encabezado, paginas in self.secciones(salida).items():
            if encabezado.startswith(prefijo):
                return paginas
        self.fail(f"no hay ninguna sección '{prefijo}' en:\n{salida}")

    def test_distingue_los_cuatro_estados(self):
        raiz = self.proyecto("Cualquiera")
        self.ejecutar("pull", "--all")
        self.editar_local(raiz, "taller/albaran.md", "Lo que escribí yo.")
        self.transporte.editar(
            self.identificador(raiz, "taller/diagnostico/auditoria-de-ruido.md"), "Ruido nuevo."
        )
        conflictiva = self.identificador(raiz, "taller/diagnostico.md")
        self.editar_local(raiz, "taller/diagnostico.md", "Lo que escribí yo.")
        self.transporte.editar(conflictiva, "Lo que escribió mi socio.")

        salida = self.salida("status", "--all")

        self.assertEqual(self.seccion(salida, "sucias"), ["wiki/taller/albaran.md"])
        self.assertEqual(
            self.seccion(salida, "remota adelantada"),
            ["wiki/taller/diagnostico/auditoria-de-ruido.md"],
        )
        self.assertEqual(self.seccion(salida, "en conflicto"), ["wiki/taller/diagnostico.md"])
        self.assertIn("1 limpia", salida)

    def test_con_todo_al_dia_no_senala_ninguna_pagina(self):
        raiz = self.proyecto("Taller")
        self.ejecutar("pull")

        salida = self.salida("status")

        self.assertEqual(self.secciones(salida), {})
        self.assertNotIn("\n  ", salida)
        self.assertIn("3 limpias", salida)
        self.assertEqual(self.ejecutar("status"), 0)

    def test_un_manifiesto_heredado_no_deja_las_paginas_sin_contar(self):
        raiz = self.proyecto("Taller")
        self.ejecutar("pull")
        self.degradar_manifiesto(raiz)
        mio = self.editar_local(raiz, "albaran.md", "Lo que escribí yo.")

        salida = self.salida("status")

        self.assertEqual(self.seccion(salida, "sucias"), ["wiki/albaran.md"])
        self.assertIn("2 limpias", salida)
        self.assertEqual(self.pagina(raiz, "albaran.md"), mio)

    def test_una_pagina_borrada_en_local_sale_como_sucia(self):
        raiz = self.proyecto("Taller")
        self.ejecutar("pull")
        (raiz / "wiki" / "albaran.md").unlink()

        salida = self.salida("status")

        self.assertEqual(self.seccion(salida, "sucias"), ["wiki/albaran.md (borrada aquí)"])

    def test_una_pagina_que_muevo_de_carpeta_sale_como_sucia_en_su_sitio_nuevo(self):
        raiz = self.proyecto("Taller")
        self.ejecutar("pull")
        (raiz / "wiki" / "albaran.md").rename(raiz / "wiki" / "diagnostico" / "albaran.md")

        salida = self.salida("status")

        self.assertEqual(
            self.seccion(salida, "sucias"),
            ["wiki/diagnostico/albaran.md (movida desde albaran.md)"],
        )
        self.assertNotIn("nuevas", salida)
        self.assertIn("2 limpias", salida)

    def test_un_fichero_nuevo_sale_como_pendiente_de_subir(self):
        raiz = self.proyecto("Taller")
        self.ejecutar("pull")
        (raiz / "wiki" / "mis-notas.md").write_text("# Mis notas\n", encoding="utf-8")

        salida = self.salida("status")

        self.assertEqual(self.seccion(salida, "nuevas"), ["wiki/mis-notas.md"])

    def test_una_pagina_con_identidad_fuera_del_manifiesto_sale_como_sucia(self):
        raiz = self.proyecto("Taller")
        self.ejecutar("pull")
        identificador = self.identificador(raiz, "albaran.md")
        pagina = self.pagina(raiz, "albaran.md")
        # Como si el proyecto viniera recién clonado: la wiki está, el estado local no.
        (raiz / ".outline" / "base" / "albaran.md").unlink()
        estado = self.estado(raiz)
        del estado["documents"][identificador]
        (raiz / ".outline" / "manifest.json").write_text(json.dumps(estado), encoding="utf-8")
        (raiz / "wiki" / "albaran.md").write_text(pagina + "\nLo que escribí yo.\n", encoding="utf-8")

        salida = self.salida("status")

        self.assertEqual(self.seccion(salida, "sucias"), ["wiki/albaran.md"])
        self.assertNotIn("nuevas", salida)

    def test_sin_manifiesto_pide_un_pull(self):
        self.proyecto("Taller")

        self.assertIn("outline pull", self.ejecutar_fallando("status"))


class Push(Caso):
    def preparar(self):
        raiz = self.proyecto("Taller")
        self.ejecutar("pull")
        self.transporte.peticiones.clear()
        return raiz

    def test_sube_solo_las_paginas_sucias(self):
        raiz = self.preparar()
        self.editar_local(raiz, "diagnostico.md", "Una línea mía.")

        self.ejecutar("push")

        escrituras = self.transporte.escrituras()
        self.assertEqual([e for e, _ in escrituras], ["documents.update"])
        self.assertEqual(escrituras[0][1]["id"], self.identificador(raiz, "diagnostico.md"))

    def test_sin_cambios_no_escribe_nada(self):
        self.preparar()

        self.ejecutar("push")

        self.assertEqual(self.transporte.escrituras(), [])

    def test_lo_que_sube_es_lo_que_tengo_en_local(self):
        raiz = self.preparar()
        self.editar_local(raiz, "diagnostico.md", "Una línea mía.")

        self.ejecutar("push")

        documento = self.transporte.documentos[self.identificador(raiz, "diagnostico.md")]
        self.assertEqual(documento["text"], "Cuerpo del diagnóstico.\n\nUna línea mía.")
        self.assertEqual(documento["title"], "Diagnóstico")

    def test_el_cuerpo_que_sube_no_lleva_el_frontmatter(self):
        raiz = self.preparar()
        self.editar_local(raiz, "diagnostico.md", "Una línea mía.")
        self.crear_local(raiz, "nota.md", "# Nota\n\nRecién escrita.\n")

        self.ejecutar("push")

        for endpoint, payload in self.transporte.escrituras():
            self.assertNotIn("outline_id", json.dumps(payload), endpoint)
            self.assertFalse(payload.get("text", "").startswith("---"), endpoint)

    def test_el_cuerpo_que_sube_no_repite_el_titulo_como_encabezado(self):
        raiz = self.preparar()
        self.editar_local(raiz, "diagnostico.md", "Una línea mía.")

        self.ejecutar("push")

        self.assertNotIn("# Diagnóstico", self.transporte.escrituras()[0][1]["text"])

    def test_tras_subir_la_pagina_queda_limpia(self):
        raiz = self.preparar()
        local = self.editar_local(raiz, "diagnostico.md", "Una línea mía.")

        self.ejecutar("push")

        self.assertEqual(self.base(raiz, "diagnostico.md"), outline.strip_frontmatter(local))
        self.assertIn("0 sucias", self.salida("status"))

    def test_tras_subir_el_manifiesto_guarda_la_revision_nueva(self):
        raiz = self.preparar()
        self.editar_local(raiz, "diagnostico.md", "Una línea mía.")

        self.ejecutar("push")

        identificador = self.identificador(raiz, "diagnostico.md")
        revision = self.transporte.documentos[identificador]["revision"]
        self.assertEqual(self.estado(raiz)["documents"][identificador]["revision"], revision)

    def test_el_pull_siguiente_no_deshace_lo_subido(self):
        raiz = self.preparar()
        local = self.editar_local(raiz, "diagnostico.md", "Una línea mía.")

        self.ejecutar("push")
        self.ejecutar("pull")

        self.assertEqual(self.pagina(raiz, "diagnostico.md"), local)

    def test_se_niega_si_la_remota_se_adelanto(self):
        raiz = self.preparar()
        identificador = self.identificador(raiz, "diagnostico.md")
        mio = self.editar_local(raiz, "diagnostico.md", "Una línea mía.")
        self.transporte.editar(
            identificador, "Cuerpo del diagnóstico.\n\nLo que escribió mi socia."
        )

        salida = self.salida("push")

        self.assertEqual(self.transporte.escrituras(), [])
        self.assertIn("conflicto", salida.lower())
        self.assertIn("wiki/diagnostico.md", salida)
        self.assertEqual(self.pagina(raiz, "diagnostico.md"), mio)
        self.assertIn(
            "Lo que escribió mi socia.", self.transporte.documentos[identificador]["text"]
        )

    def test_una_pagina_en_conflicto_no_frena_a_las_demas(self):
        raiz = self.preparar()
        self.editar_local(raiz, "diagnostico.md", "Una línea mía.")
        self.editar_local(raiz, "albaran.md", "Otra línea mía.")
        self.transporte.editar(self.identificador(raiz, "diagnostico.md"), "Lo de mi socia.")

        self.ejecutar("push")

        subidos = [p["id"] for _, p in self.transporte.escrituras()]
        self.assertEqual(subidos, [self.identificador(raiz, "albaran.md")])

    def test_un_fichero_sin_identidad_crea_la_pagina(self):
        raiz = self.preparar()
        self.crear_local(raiz, "presupuesto.md", "# Presupuesto\n\nLo que cuesta.\n")

        self.ejecutar("push")

        endpoint, payload = self.transporte.escrituras()[0]
        self.assertEqual(endpoint, "documents.create")
        self.assertEqual(payload["title"], "Presupuesto")
        self.assertEqual(payload["text"], "Lo que cuesta.")
        self.assertEqual(payload["collectionId"], "col-1")
        self.assertTrue(payload["publish"])
        self.assertIsNone(payload.get("parentDocumentId"))

    def test_el_identificador_devuelto_se_escribe_en_el_frontmatter(self):
        raiz = self.preparar()
        self.crear_local(raiz, "presupuesto.md", "# Presupuesto\n\nLo que cuesta.\n")

        self.ejecutar("push")

        identificador = max(self.transporte.documentos)
        self.assertEqual(
            self.pagina(raiz, "presupuesto.md"),
            f"---\noutline_id: {identificador}\n---\n\n# Presupuesto\n\nLo que cuesta.\n",
        )
        self.assertEqual(self.estado(raiz)["documents"][identificador]["path"], "presupuesto.md")

    def test_una_pagina_recien_creada_queda_limpia(self):
        raiz = self.preparar()
        self.crear_local(raiz, "presupuesto.md", "# Presupuesto\n\nLo que cuesta.\n")

        self.ejecutar("push")

        salida = self.salida("status")
        self.assertNotIn("presupuesto.md", salida)
        self.assertIn("0 sucias", salida)
        self.assertEqual(self.base(raiz, "presupuesto.md"), "# Presupuesto\n\nLo que cuesta.\n")

    def test_la_carpeta_dice_de_que_pagina_cuelga(self):
        raiz = self.preparar()
        self.crear_local(raiz, "diagnostico/compresion.md", "# Compresión\n\nLos cilindros.\n")

        self.ejecutar("push")

        _, payload = self.transporte.escrituras()[0]
        self.assertEqual(payload["parentDocumentId"], self.identificador(raiz, "diagnostico.md"))
        self.assertEqual(payload["collectionId"], "col-1")

    def test_con_all_la_coleccion_sale_del_primer_segmento(self):
        raiz = self.proyecto("Cualquiera")
        self.ejecutar("pull", "--all")
        self.transporte.peticiones.clear()
        self.crear_local(raiz, "almacen-nuble/roturas.md", "# Roturas\n\nLo que se rompió.\n")

        self.ejecutar("push", "--all")

        _, payload = self.transporte.escrituras()[0]
        self.assertEqual(payload["collectionId"], "col-2")
        self.assertIsNone(payload.get("parentDocumentId"))

    def test_una_pagina_nueva_sin_madre_no_se_crea_y_no_frena_a_las_demas(self):
        raiz = self.preparar()
        self.crear_local(raiz, "chapa/pintura.md", "# Pintura\n\nEl color.\n")
        self.editar_local(raiz, "albaran.md", "Una línea mía.")

        salida = self.salida("push")

        self.assertIn("wiki/chapa/pintura.md", salida)
        self.assertIn("wiki/chapa.md", salida)
        subidos = [p["id"] for _, p in self.transporte.escrituras()]
        self.assertEqual(subidos, [self.identificador(raiz, "albaran.md")])

    def test_una_pagina_nueva_sin_encabezado_no_se_crea_y_lo_dice(self):
        raiz = self.preparar()
        self.crear_local(raiz, "presupuesto.md", "Lo que cuesta, sin título.\n")

        salida = self.salida("push")

        self.assertIn("wiki/presupuesto.md", salida)
        self.assertIn("encabezado", salida)
        self.assertEqual(self.transporte.escrituras(), [])

    def test_las_paginas_nuevas_se_crean_de_fuera_hacia_dentro_y_en_orden(self):
        raiz = self.preparar()
        self.crear_local(raiz, "presupuesto.md", "# Presupuesto\n\nLo que cuesta.\n")
        self.crear_local(raiz, "chapa.md", "# Chapa\n\nEl taller de chapa.\n")
        self.crear_local(raiz, "chapa/pintura.md", "# Pintura\n\nEl color.\n")

        self.ejecutar("push")

        creadas = [p["title"] for e, p in self.transporte.escrituras() if e == "documents.create"]
        self.assertEqual(creadas, ["Chapa", "Presupuesto", "Pintura"])

    def test_una_pagina_nueva_acaba_al_final_de_su_nivel(self):
        raiz = self.preparar()
        self.crear_local(raiz, "presupuesto.md", "# Presupuesto\n\nLo que cuesta.\n")

        self.ejecutar("push")
        self.ejecutar("pull")

        indice = (raiz / ".outline" / "index.md").read_text(encoding="utf-8")
        titulos = [re.search(r"\[(.+?)\]", l).group(1) for l in indice.splitlines() if "- [" in l]
        self.assertEqual(titulos, ["Diagnóstico", "Auditoría de ruido", "Albarán", "Presupuesto"])

    def test_una_pagina_nueva_anidada_acaba_al_final_de_su_nivel(self):
        raiz = self.preparar()
        self.crear_local(raiz, "diagnostico/revision.md", "# Revisión\n\nLo revisado.\n")

        self.ejecutar("push")
        self.ejecutar("pull")

        indice = (raiz / ".outline" / "index.md").read_text(encoding="utf-8")
        hijas = [re.search(r"\[(.+?)\]", l).group(1) for l in indice.splitlines() if "  - [" in l]
        self.assertEqual(hijas, ["Auditoría de ruido", "Revisión"])

    def test_la_revision_que_se_apunta_es_la_de_despues_de_colocarla(self):
        raiz = self.preparar()
        self.crear_local(raiz, "presupuesto.md", "# Presupuesto\n\nLo que cuesta.\n")

        self.ejecutar("push")

        identificador = self.identificador(raiz, "presupuesto.md")
        apuntada = self.estado(raiz)["documents"][identificador]["revision"]
        self.assertEqual(apuntada, self.transporte.documentos[identificador]["revision"])

    def test_la_pagina_recien_creada_se_vuelve_a_subir_sin_conflicto_falso(self):
        raiz = self.preparar()
        self.crear_local(raiz, "presupuesto.md", "# Presupuesto\n\nLo que cuesta.\n")
        self.ejecutar("push")

        self.editar_local(raiz, "presupuesto.md", "Y una línea más.")
        salida = self.salida("push")

        self.assertIn("1 página subida", salida)
        self.assertNotIn("en conflicto", salida)

    def test_una_pagina_nueva_cuelga_de_otra_pagina_nueva(self):
        raiz = self.preparar()
        self.crear_local(raiz, "chapa.md", "# Chapa\n\nEl taller de chapa.\n")
        self.crear_local(raiz, "chapa/pintura.md", "# Pintura\n\nEl color.\n")

        self.ejecutar("push")
        self.ejecutar("pull")

        padre = self.identificador(raiz, "chapa.md")
        creaciones = [p for e, p in self.transporte.escrituras() if e == "documents.create"]
        self.assertEqual(creaciones[1]["parentDocumentId"], padre)
        self.assertTrue((raiz / "wiki" / "chapa" / "pintura.md").is_file())

    def test_dos_ficheros_con_la_misma_identidad_abortan_antes_de_escribir(self):
        raiz = self.preparar()
        copia = self.pagina(raiz, "diagnostico.md")
        self.crear_local(raiz, "copia-del-diagnostico.md", copia.replace("Cuerpo", "Otro cuerpo"))

        mensaje = self.ejecutar_fallando("push")

        self.assertIn("wiki/diagnostico.md", mensaje)
        self.assertIn("wiki/copia-del-diagnostico.md", mensaje)
        self.assertEqual(self.transporte.escrituras(), [])

    def test_el_frontmatter_manda_sobre_la_ruta_que_el_manifiesto_recuerda(self):
        raiz = self.preparar()
        original = self.pagina(raiz, "diagnostico.md")
        diagnostico = self.identificador(raiz, "diagnostico.md")
        self.crear_local(raiz, "copia-del-diagnostico.md", original)
        (raiz / "wiki" / "diagnostico.md").write_text(
            outline.strip_frontmatter(original), encoding="utf-8"
        )

        self.ejecutar("push")

        documentos = self.estado(raiz)["documents"]
        self.assertEqual(documentos[diagnostico]["path"], "copia-del-diagnostico.md")
        self.assertNotEqual(self.identificador(raiz, "diagnostico.md"), diagnostico)
        self.assertEqual(self.transporte.papelera, [])

    def test_un_cuerpo_que_empieza_por_tres_guiones_no_se_pierde_al_subir(self):
        raiz = self.preparar()
        identificador = self.identificador(raiz, "diagnostico.md")
        (raiz / "wiki" / "diagnostico.md").write_text(
            outline.frontmatter(identificador)
            + "---\nnota: esto es cuerpo, no metadatos\n---\n\n# Diagnóstico\n\nCuerpo.\n",
            encoding="utf-8",
        )

        self.ejecutar("push")

        texto = self.transporte.documentos[identificador]["text"]
        self.assertIn("nota: esto es cuerpo, no metadatos", texto)

    def test_se_niega_si_la_revision_avanzo_aunque_el_cuerpo_vuelva_a_ser_el_de_la_base(self):
        raiz = self.preparar()
        identificador = self.identificador(raiz, "diagnostico.md")
        original = self.transporte.documentos[identificador]["text"]
        self.editar_local(raiz, "diagnostico.md", "Lo que escribí yo.")
        self.transporte.editar(identificador, "Un desvío de mi socia.")
        self.transporte.editar(identificador, original)

        salida = self.salida("push")

        self.assertIn("en conflicto", salida)
        self.assertEqual(self.transporte.escrituras(), [])

    def test_una_pagina_que_se_fue_a_otra_coleccion_no_se_apunta_en_la_de_aqui(self):
        raiz = self.preparar()
        identificador = self.identificador(raiz, "diagnostico.md")
        self.editar_local(raiz, "diagnostico.md", "Una línea mía.")
        self.transporte.documentos[identificador]["collectionId"] = "col-2"

        salida = self.salida("push")

        self.assertIn("sin comparar", salida)
        self.assertEqual(self.transporte.escrituras(), [])
        self.assertEqual(self.estado(raiz)["documents"][identificador]["collection"], "Taller")

    def test_si_el_ultimo_pull_no_termino_el_push_lo_avisa(self):
        raiz = self.preparar()
        self.transporte.inaccesibles.add(self.identificador_remoto("Albarán"))
        self.ejecutar("pull")
        self.editar_local(raiz, "diagnostico.md", "Una línea mía.")

        salida = self.salida("push")

        self.assertIn("aviso", salida)
        self.assertIn("outline pull", salida)

    def test_sin_cambios_no_le_pregunta_a_outline_por_ninguna_pagina(self):
        self.preparar()

        self.ejecutar("push")

        pedidas = [e for e, _ in self.transporte.peticiones if e == "documents.info"]
        self.assertEqual(pedidas, [])

    def test_lo_que_ya_esta_en_outline_solo_adelanta_la_base(self):
        raiz = self.preparar()
        local = self.editar_local(raiz, "diagnostico.md", "Una línea mía.")
        self.transporte.editar(
            self.identificador(raiz, "diagnostico.md"),
            "Cuerpo del diagnóstico.\n\nUna línea mía.",
        )

        salida = self.salida("push")

        self.assertEqual(self.transporte.escrituras(), [])
        self.assertIn("ya estaba en Outline", salida)
        self.assertEqual(self.base(raiz, "diagnostico.md"), outline.strip_frontmatter(local))
        self.assertIn("0 sucias", self.salida("status"))

    def montar_sin_base(self):
        """Una página cambiada aquí y en Outline, y de la que se perdió la base."""
        raiz = self.preparar()
        self.olvidar(raiz, "diagnostico.md")
        identificador = self.identificador_remoto("Diagnóstico")
        self.editar_local(raiz, "diagnostico.md", "Lo que escribí yo.")
        self.transporte.editar(identificador, "Cuerpo del diagnóstico.\n\nLo de mi socia.")
        return raiz, identificador

    def test_una_pagina_sin_base_no_se_sube_a_ciegas(self):
        _, identificador = self.montar_sin_base()

        salida = self.salida("push")

        self.assertEqual(self.transporte.escrituras(), [])
        self.assertIn("wiki/diagnostico.md", salida)
        self.assertIn("outline resolve", salida)
        self.assertIn("Lo de mi socia.", self.transporte.documentos[identificador]["text"])

    def test_resuelta_esa_pagina_el_push_siguiente_la_sube(self):
        _, identificador = self.montar_sin_base()

        self.ejecutar("resolve", "Diagnóstico")
        self.ejecutar("push")

        texto = self.transporte.documentos[identificador]["text"]
        self.assertIn("Lo que escribí yo.", texto)
        self.assertNotIn("Lo de mi socia.", texto)

    def test_si_el_frontmatter_se_perdio_el_identificador_sale_del_manifiesto(self):
        raiz = self.preparar()
        identificador = self.identificador(raiz, "diagnostico.md")
        (raiz / "wiki" / "diagnostico.md").write_text(
            "# Diagnóstico\n\nCuerpo del diagnóstico.\n\nUna línea mía.\n", encoding="utf-8"
        )

        self.ejecutar("push")

        escrituras = self.transporte.escrituras()
        self.assertEqual([e for e, _ in escrituras], ["documents.update"])
        self.assertEqual(escrituras[0][1]["id"], identificador)
        self.assertIn("Una línea mía.", self.transporte.documentos[identificador]["text"])

    def test_sin_manifiesto_pide_un_pull(self):
        self.proyecto("Taller")

        self.assertIn("outline pull", self.ejecutar_fallando("push"))

    def test_las_paginas_de_otra_coleccion_no_se_tocan(self):
        raiz = self.proyecto("Cualquiera")
        self.ejecutar("pull", "--all")
        self.editar_local(raiz, "almacen-nuble/inventario.md", "Una línea mía.")
        self.transporte.peticiones.clear()

        self.ejecutar("push", "--all")

        subidos = [p["id"] for _, p in self.transporte.escrituras()]
        self.assertEqual(subidos, [self.identificador(raiz, "almacen-nuble/inventario.md")])


class PushMueve(Caso):
    def preparar(self):
        raiz = self.proyecto("Taller")
        self.ejecutar("pull")
        self.transporte.peticiones.clear()
        return raiz

    def test_mover_el_fichero_a_otra_carpeta_reanida_la_pagina(self):
        raiz = self.preparar()
        albaran = self.identificador(raiz, "albaran.md")
        diagnostico = self.identificador(raiz, "diagnostico.md")
        self.mover_fichero(raiz, "albaran.md", "diagnostico/albaran.md")

        self.ejecutar("push")

        escrituras = self.transporte.escrituras()
        self.assertEqual([e for e, _ in escrituras], ["documents.move"])
        self.assertEqual(escrituras[0][1]["id"], albaran)
        self.assertEqual(escrituras[0][1]["parentDocumentId"], diagnostico)
        self.assertEqual(self.transporte.documentos[albaran]["parentDocumentId"], diagnostico)

    def test_sacar_el_fichero_a_la_raiz_descuelga_la_pagina(self):
        raiz = self.preparar()
        auditoria = self.identificador(raiz, "diagnostico/auditoria-de-ruido.md")
        self.mover_fichero(raiz, "diagnostico/auditoria-de-ruido.md", "auditoria-de-ruido.md")

        self.ejecutar("push")

        endpoint, payload = self.transporte.escrituras()[0]
        self.assertEqual(endpoint, "documents.move")
        self.assertNotIn("parentDocumentId", payload)
        self.assertEqual(payload["collectionId"], "col-1")
        self.assertIsNone(self.transporte.documentos[auditoria]["parentDocumentId"])

    def test_mover_no_toca_el_texto_de_la_pagina(self):
        raiz = self.preparar()
        albaran = self.identificador(raiz, "albaran.md")
        self.mover_fichero(raiz, "albaran.md", "diagnostico/albaran.md")

        self.ejecutar("push")

        self.assertEqual(self.transporte.documentos[albaran]["text"], "Cuerpo del albarán.")

    def test_tras_mover_el_manifiesto_apunta_a_la_ruta_nueva(self):
        raiz = self.preparar()
        albaran = self.identificador(raiz, "albaran.md")
        self.mover_fichero(raiz, "albaran.md", "diagnostico/albaran.md")

        self.ejecutar("push")

        entrada = self.estado(raiz)["documents"][albaran]
        self.assertEqual(entrada["path"], "diagnostico/albaran.md")

    def test_tras_mover_la_base_se_muda_con_el_fichero(self):
        raiz = self.preparar()
        self.mover_fichero(raiz, "albaran.md", "diagnostico/albaran.md")

        self.ejecutar("push")

        self.assertEqual(
            self.base(raiz, "diagnostico/albaran.md"), "# Albarán\n\nCuerpo del albarán.\n"
        )
        self.assertFalse((raiz / ".outline" / "base" / "albaran.md").exists())

    def test_tras_mover_la_pagina_queda_limpia(self):
        raiz = self.preparar()
        self.mover_fichero(raiz, "albaran.md", "diagnostico/albaran.md")
        self.ejecutar("push")

        salida = self.salida("status")

        self.assertIn("3 limpias", salida)

    def test_la_revision_que_se_apunta_es_la_de_despues_del_movimiento(self):
        raiz = self.preparar()
        albaran = self.identificador(raiz, "albaran.md")
        self.mover_fichero(raiz, "albaran.md", "diagnostico/albaran.md")

        self.ejecutar("push")

        apuntada = self.estado(raiz)["documents"][albaran]["revision"]
        self.assertEqual(apuntada, self.transporte.documentos[albaran]["revision"])

    def test_moverla_no_deja_un_conflicto_falso_en_el_push_siguiente(self):
        raiz = self.preparar()
        self.mover_fichero(raiz, "albaran.md", "diagnostico/albaran.md")
        self.ejecutar("push")
        self.editar_local(raiz, "diagnostico/albaran.md", "Una línea mía.")

        salida = self.salida("push")

        self.assertIn("1 página subida", salida)
        self.assertNotIn("en conflicto", salida)

    def test_el_pull_siguiente_no_devuelve_la_pagina_a_su_sitio(self):
        raiz = self.preparar()
        self.mover_fichero(raiz, "albaran.md", "diagnostico/albaran.md")
        self.ejecutar("push")

        salida = self.salida("pull")

        self.assertTrue((raiz / "wiki" / "diagnostico" / "albaran.md").is_file())
        self.assertFalse((raiz / "wiki" / "albaran.md").exists())
        self.assertIn("todo al día", salida)

    def test_mover_y_editar_en_la_misma_pasada(self):
        raiz = self.preparar()
        albaran = self.identificador(raiz, "albaran.md")
        self.mover_fichero(raiz, "albaran.md", "diagnostico/albaran.md")
        self.editar_local(raiz, "diagnostico/albaran.md", "Una línea mía.")

        self.ejecutar("push")

        self.assertEqual(
            [e for e, _ in self.transporte.escrituras()],
            ["documents.update", "documents.move"],
        )
        self.assertIn("Una línea mía.", self.transporte.documentos[albaran]["text"])
        entrada = self.estado(raiz)["documents"][albaran]
        self.assertEqual(entrada["path"], "diagnostico/albaran.md")
        self.assertFalse((raiz / ".outline" / "base" / "albaran.md").exists())

    def test_cambiar_solo_el_nombre_del_fichero_no_mueve_nada(self):
        raiz = self.preparar()
        albaran = self.identificador(raiz, "albaran.md")
        self.mover_fichero(raiz, "albaran.md", "albaran-de-2026.md")

        self.ejecutar("push")

        self.assertEqual(self.transporte.escrituras(), [])
        self.assertEqual(
            self.estado(raiz)["documents"][albaran]["path"], "albaran-de-2026.md"
        )

    def test_el_nombre_del_fichero_vuelve_al_del_titulo_en_el_pull_siguiente(self):
        raiz = self.preparar()
        self.mover_fichero(raiz, "albaran.md", "albaran-de-2026.md")
        self.ejecutar("push")

        self.ejecutar("pull")

        self.assertTrue((raiz / "wiki" / "albaran.md").is_file())
        self.assertFalse((raiz / "wiki" / "albaran-de-2026.md").exists())

    def test_mover_una_pagina_con_hijas_se_lleva_el_arbol_de_una_vez(self):
        raiz = self.preparar()
        diagnostico = self.identificador(raiz, "diagnostico.md")
        albaran = self.identificador(raiz, "albaran.md")
        auditoria = self.identificador(raiz, "diagnostico/auditoria-de-ruido.md")
        self.mover_fichero(raiz, "diagnostico.md", "albaran/diagnostico.md")
        (raiz / "wiki" / "diagnostico").rename(raiz / "wiki" / "albaran" / "diagnostico")

        self.ejecutar("push")

        escrituras = self.transporte.escrituras()
        self.assertEqual([(e, p["id"]) for e, p in escrituras], [("documents.move", diagnostico)])
        self.assertEqual(escrituras[0][1]["parentDocumentId"], albaran)
        entradas = self.estado(raiz)["documents"]
        self.assertEqual(entradas[auditoria]["path"], "albaran/diagnostico/auditoria-de-ruido.md")

    def test_un_movimiento_que_outline_no_confirma_no_se_da_por_bueno(self):
        raiz = self.preparar()
        albaran = self.identificador(raiz, "albaran.md")
        self.transporte.mudos.add(albaran)
        self.mover_fichero(raiz, "albaran.md", "diagnostico/albaran.md")

        salida = self.salida("push")

        self.assertIn("sin mover", salida)
        self.assertEqual(self.estado(raiz)["documents"][albaran]["path"], "albaran.md")

    def adelantar(self, raiz):
        """Mi socia edita en Outline la página que voy a mover, entre mi pull y mi push."""
        albaran = self.identificador(raiz, "albaran.md")
        self.transporte.editar(albaran, "Lo que escribió mi socia.")
        self.mover_fichero(raiz, "albaran.md", "diagnostico/albaran.md")
        return albaran

    def test_mover_con_outline_adelantado_no_apunta_la_revision_del_movimiento(self):
        raiz = self.preparar()
        albaran = self.adelantar(raiz)

        salida = self.salida("push")

        self.assertIn("adelantada", salida)
        apuntada = self.estado(raiz)["documents"][albaran]["revision"]
        self.assertNotEqual(apuntada, self.transporte.documentos[albaran]["revision"])

    def test_movida_asi_el_pull_siguiente_trae_el_texto_de_mi_socia(self):
        raiz = self.preparar()
        self.adelantar(raiz)
        self.ejecutar("push")

        self.ejecutar("pull")

        self.assertIn("Lo que escribió mi socia.", self.pagina(raiz, "diagnostico/albaran.md"))

    def test_movida_asi_una_edicion_mia_encima_se_rebota_como_conflicto(self):
        raiz = self.preparar()
        albaran = self.adelantar(raiz)
        self.ejecutar("push")
        self.editar_local(raiz, "diagnostico/albaran.md", "Lo que escribí yo.")

        salida = self.salida("push")

        self.assertIn("en conflicto", salida)
        self.assertNotIn("Lo que escribí yo.", self.transporte.documentos[albaran]["text"])

    def test_una_pagina_en_conflicto_no_se_mueve(self):
        raiz = self.preparar()
        albaran = self.identificador(raiz, "albaran.md")
        self.mover_fichero(raiz, "albaran.md", "diagnostico/albaran.md")
        self.editar_local(raiz, "diagnostico/albaran.md", "Lo que escribí yo.")
        self.transporte.editar(albaran, "Lo que escribió mi socia.")

        salida = self.salida("push")

        self.assertEqual(self.transporte.escrituras(), [])
        self.assertIn("en conflicto", salida)
        self.assertEqual(self.estado(raiz)["documents"][albaran]["path"], "albaran.md")

    def test_mover_bajo_una_pagina_recien_creada(self):
        raiz = self.preparar()
        albaran = self.identificador(raiz, "albaran.md")
        self.crear_local(raiz, "chapa.md", "# Chapa\n\nEl taller de chapa.\n")
        self.mover_fichero(raiz, "albaran.md", "chapa/albaran.md")

        self.ejecutar("push")

        endpoint, payload = self.transporte.escrituras()[-1]
        self.assertEqual((endpoint, payload["id"]), ("documents.move", albaran))
        self.assertEqual(payload["parentDocumentId"], self.identificador(raiz, "chapa.md"))

    def test_mover_a_otra_coleccion_con_all(self):
        raiz = self.proyecto("Cualquiera")
        self.ejecutar("pull", "--all")
        albaran = self.identificador(raiz, "taller/albaran.md")
        self.transporte.peticiones.clear()
        self.mover_fichero(raiz, "taller/albaran.md", "almacen-nuble/albaran.md")

        self.ejecutar("push", "--all")

        endpoint, payload = self.transporte.escrituras()[0]
        self.assertEqual(endpoint, "documents.move")
        self.assertEqual(payload["collectionId"], "col-2")
        self.assertNotIn("parentDocumentId", payload)
        entrada = self.estado(raiz)["documents"][albaran]
        self.assertEqual(entrada["collection"], "Almacén Ñuble")
        self.assertEqual(entrada["collectionId"], "col-2")

    def test_mover_deja_la_pagina_al_final_de_su_nivel(self):
        raiz = self.preparar()
        self.mover_fichero(raiz, "diagnostico/auditoria-de-ruido.md", "auditoria-de-ruido.md")

        self.ejecutar("push")
        self.ejecutar("pull")

        indice = (raiz / ".outline" / "index.md").read_text(encoding="utf-8")
        titulos = [re.search(r"\[(.+?)\]", l).group(1) for l in indice.splitlines() if "- [" in l]
        self.assertEqual(titulos, ["Diagnóstico", "Albarán", "Auditoría de ruido"])

    def test_mover_a_una_carpeta_sin_pagina_madre_no_mueve_nada(self):
        raiz = self.preparar()
        albaran = self.identificador(raiz, "albaran.md")
        self.mover_fichero(raiz, "albaran.md", "chapa/albaran.md")

        salida = self.salida("push")

        self.assertEqual(self.transporte.escrituras(), [])
        self.assertIn("wiki/chapa.md", salida)
        self.assertEqual(self.estado(raiz)["documents"][albaran]["path"], "albaran.md")

    def test_dos_ficheros_que_intercambian_sus_rutas_no_se_pisan_la_base(self):
        raiz = self.preparar()
        wiki = raiz / "wiki"
        (wiki / "albaran.md").rename(wiki / "en-transito.md")
        (wiki / "diagnostico.md").rename(wiki / "albaran.md")
        (wiki / "en-transito.md").rename(wiki / "diagnostico.md")

        self.ejecutar("push")

        self.assertIn("Cuerpo del diagnóstico.", self.base(raiz, "albaran.md"))
        self.assertIn("Cuerpo del albarán.", self.base(raiz, "diagnostico.md"))
        self.assertEqual(self.salida("status").count("sucia"), 1)

    def test_crear_una_pagina_en_el_hueco_que_deja_la_que_se_muda(self):
        raiz = self.preparar()
        albaran = self.identificador(raiz, "albaran.md")
        diagnostico = self.identificador(raiz, "diagnostico.md")
        self.mover_fichero(raiz, "albaran.md", "diagnostico/albaran.md")
        self.crear_local(raiz, "albaran.md", "# Albaranes de 2026\n\nLos de este año.\n")

        self.ejecutar("push")

        self.assertEqual(self.transporte.documentos[albaran]["parentDocumentId"], diagnostico)
        nuevo = self.identificador_remoto("Albaranes de 2026")
        self.assertIsNone(self.transporte.documentos[nuevo]["parentDocumentId"])
        self.assertEqual(self.transporte.papelera, [])

    def test_un_fichero_en_una_carpeta_que_no_es_ninguna_coleccion_no_se_mueve(self):
        raiz = self.proyecto("Cualquiera")
        self.ejecutar("pull", "--all")
        self.transporte.peticiones.clear()
        self.mover_fichero(raiz, "taller/albaran.md", "albaran.md")

        salida = self.salida("push", "--all")

        self.assertEqual(self.transporte.escrituras(), [])
        self.assertIn("sin mover", salida)


class PushRenombra(Caso):
    def renombrar(self, raiz, ruta, viejo, nuevo):
        """Cambia el encabezado de nivel 1, que es donde vive el título."""
        pagina = raiz / "wiki" / ruta
        texto = pagina.read_text(encoding="utf-8").replace(f"# {viejo}", f"# {nuevo}")
        pagina.write_text(texto, encoding="utf-8")

    def test_cambiar_el_encabezado_de_nivel_1_renombra_la_pagina(self):
        raiz = self.proyecto("Taller")
        self.ejecutar("pull")
        albaran = self.identificador(raiz, "albaran.md")
        self.renombrar(raiz, "albaran.md", "Albarán", "Albarán de entrega")

        self.ejecutar("push")

        self.assertEqual(self.transporte.documentos[albaran]["title"], "Albarán de entrega")

    def test_el_titulo_no_se_queda_dentro_del_cuerpo(self):
        raiz = self.proyecto("Taller")
        self.ejecutar("pull")
        albaran = self.identificador(raiz, "albaran.md")
        self.renombrar(raiz, "albaran.md", "Albarán", "Albarán de entrega")

        self.ejecutar("push")

        self.assertNotIn("#", self.transporte.documentos[albaran]["text"])

    def test_tras_renombrar_el_pull_mueve_el_fichero_al_nombre_nuevo(self):
        raiz = self.proyecto("Taller")
        self.ejecutar("pull")
        self.renombrar(raiz, "albaran.md", "Albarán", "Albarán de entrega")
        self.ejecutar("push")

        self.ejecutar("pull")

        self.assertTrue((raiz / "wiki" / "albaran-de-entrega.md").is_file())
        self.assertFalse((raiz / "wiki" / "albaran.md").exists())

    def test_renombrar_no_cambia_de_sitio_a_la_pagina(self):
        raiz = self.proyecto("Taller")
        self.ejecutar("pull")
        self.transporte.peticiones.clear()
        self.renombrar(raiz, "diagnostico.md", "Diagnóstico", "Diagnóstico del motor")

        self.ejecutar("push")

        self.assertEqual([e for e, _ in self.transporte.escrituras()], ["documents.update"])


class PushBorra(Caso):
    def preparar(self):
        raiz = self.proyecto("Taller")
        self.ejecutar("pull")
        self.transporte.peticiones.clear()
        return raiz

    def test_borrar_el_fichero_manda_la_pagina_a_la_papelera(self):
        raiz = self.preparar()
        albaran = self.identificador(raiz, "albaran.md")
        (raiz / "wiki" / "albaran.md").unlink()

        self.ejecutar("push", "--yes")

        self.assertEqual(
            self.transporte.escrituras(),
            [("documents.delete", {"id": albaran, "permanent": False})],
        )

    def test_la_llamada_de_borrado_no_es_permanente(self):
        raiz = self.preparar()
        albaran = self.identificador(raiz, "albaran.md")
        (raiz / "wiki" / "albaran.md").unlink()

        self.ejecutar("push", "--yes")

        self.assertEqual(self.transporte.papelera, [albaran])
        self.assertEqual(self.transporte.destruidos, [])

    def test_tras_borrar_se_limpian_el_manifiesto_y_la_base(self):
        raiz = self.preparar()
        albaran = self.identificador(raiz, "albaran.md")
        (raiz / "wiki" / "albaran.md").unlink()

        self.ejecutar("push", "--yes")

        self.assertNotIn(albaran, self.estado(raiz)["documents"])
        self.assertFalse((raiz / ".outline" / "base" / "albaran.md").exists())

    def test_antes_de_borrar_lista_las_paginas_afectadas(self):
        raiz = self.preparar()
        (raiz / "wiki" / "albaran.md").unlink()
        self.teclear("si")

        salida = self.salida("push")

        self.assertIn("wiki/albaran.md", salida)
        self.assertIn("papelera", salida)
        self.assertEqual(len(self.transporte.papelera), 1)

    def test_contestando_que_no_no_borra_nada(self):
        raiz = self.preparar()
        albaran = self.identificador(raiz, "albaran.md")
        (raiz / "wiki" / "albaran.md").unlink()
        self.teclear("no")

        salida = self.salida("push")

        self.assertEqual(self.transporte.escrituras(), [])
        self.assertIn(albaran, self.estado(raiz)["documents"])
        self.assertIn("sin borrar", salida)

    def test_con_yes_tambien_lista_las_paginas_antes_de_borrarlas(self):
        raiz = self.preparar()
        (raiz / "wiki" / "albaran.md").unlink()

        salida = self.salida("push", "--yes")

        self.assertIn("ya no están en el disco", salida)
        self.assertIn("wiki/albaran.md", salida)

    def test_contestar_que_no_es_una_decision_tuya_y_no_un_fallo(self):
        raiz = self.preparar()
        (raiz / "wiki" / "albaran.md").unlink()
        self.teclear("no")

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(outline.main(["push"]), 0)

    def test_sin_nadie_al_teclado_no_borra_y_dice_como_confirmar(self):
        raiz = self.preparar()
        (raiz / "wiki" / "albaran.md").unlink()

        salida = self.salida("push")

        self.assertEqual(self.transporte.escrituras(), [])
        self.assertIn("--yes", salida)

    def test_una_pasada_con_borrados_pendientes_sale_con_error(self):
        raiz = self.preparar()
        (raiz / "wiki" / "albaran.md").unlink()

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(outline.main(["push"]), 1)

    def test_sin_la_marca_de_pull_completo_no_borra(self):
        raiz = self.preparar()
        # Un pull que no puede leer una página no deja la marca de completo.
        self.transporte.inaccesibles.add(self.identificador(raiz, "diagnostico.md"))
        self.ejecutar("pull")
        (raiz / "wiki" / "albaran.md").unlink()
        self.transporte.peticiones.clear()

        salida = self.salida("push", "--yes")

        self.assertEqual(self.transporte.escrituras(), [])
        self.assertIn("wiki/albaran.md", salida)
        self.assertIn("el último pull no terminó", salida)

    def test_completado_el_pull_ese_mismo_borrado_sale_adelante(self):
        raiz = self.preparar()
        self.transporte.inaccesibles.add(self.identificador(raiz, "diagnostico.md"))
        self.ejecutar("pull")
        (raiz / "wiki" / "albaran.md").unlink()
        self.ejecutar("push", "--yes")

        self.transporte.inaccesibles.clear()
        self.ejecutar("pull")
        self.ejecutar("push", "--yes")

        self.assertEqual(len(self.transporte.papelera), 1)

    def test_una_coleccion_fuera_del_ambito_no_se_cuenta_como_borrada(self):
        raiz = self.proyecto("Taller")
        self.ejecutar("pull", "--all")
        self.ejecutar("pull")
        inventario = self.identificador(raiz, "almacen-nuble/inventario.md")
        shutil.rmtree(raiz / "wiki" / "almacen-nuble")
        self.transporte.peticiones.clear()

        self.ejecutar("push", "--yes")

        self.assertEqual(self.transporte.escrituras(), [])
        self.assertIn(inventario, self.estado(raiz)["documents"])

    def test_una_pagina_creada_en_remoto_despues_del_pull_no_se_borra(self):
        self.preparar()
        ajena = self.transporte.crear("Presupuesto", "Lo que cuesta.")

        self.ejecutar("push", "--yes")

        self.assertEqual(self.transporte.escrituras(), [])
        self.assertIn(ajena, self.transporte.documentos)

    def test_una_pagina_que_muevo_no_se_cuenta_como_borrada(self):
        raiz = self.preparar()
        self.mover_fichero(raiz, "albaran.md", "diagnostico/albaran.md")

        self.ejecutar("push", "--yes")

        self.assertEqual([e for e, _ in self.transporte.escrituras()], ["documents.move"])

    def test_borrar_una_carpeta_entera_llama_una_sola_vez(self):
        raiz = self.preparar()
        diagnostico = self.identificador(raiz, "diagnostico.md")
        auditoria = self.identificador(raiz, "diagnostico/auditoria-de-ruido.md")
        (raiz / "wiki" / "diagnostico.md").unlink()
        shutil.rmtree(raiz / "wiki" / "diagnostico")

        self.ejecutar("push", "--yes")

        self.assertEqual(
            self.transporte.escrituras(),
            [("documents.delete", {"id": diagnostico, "permanent": False})],
        )
        self.assertEqual(self.transporte.papelera, [diagnostico, auditoria])
        entradas = self.estado(raiz)["documents"]
        self.assertEqual(list(entradas), [self.identificador(raiz, "albaran.md")])

    def test_borrar_la_madre_dejando_las_hijas_no_borra_nada(self):
        raiz = self.preparar()
        diagnostico = self.identificador(raiz, "diagnostico.md")
        (raiz / "wiki" / "diagnostico.md").unlink()

        salida = self.salida("push", "--yes")

        self.assertEqual(self.transporte.escrituras(), [])
        self.assertIn(diagnostico, self.estado(raiz)["documents"])
        self.assertIn("todavía tiene páginas colgando", salida)

    def test_borra_despues_de_subir_y_de_crear(self):
        raiz = self.preparar()
        self.editar_local(raiz, "diagnostico.md", "Una línea mía.")
        self.crear_local(raiz, "presupuesto.md", "# Presupuesto\n\nLo que cuesta.\n")
        (raiz / "wiki" / "albaran.md").unlink()

        self.ejecutar("push", "--yes")

        self.assertEqual(
            [e for e, _ in self.transporte.escrituras()],
            ["documents.update", "documents.create", "documents.move", "documents.delete"],
        )

    def test_sin_ficheros_que_falten_no_pregunta_nada(self):
        self.preparar()

        salida = self.salida("push")

        self.assertNotIn("papelera", salida)


class Marcadores(Caso):
    """En Markdown no hay compilador que avise, así que ningún comando escribe marcadores."""

    def test_ningun_comando_escribe_marcadores_de_conflicto(self):
        raiz = self.proyecto("Taller")
        self.ejecutar("pull")
        self.editar_local(raiz, "diagnostico.md", "Una línea mía.")
        self.transporte.editar(
            self.identificador(raiz, "diagnostico.md"), "Lo que escribió mi socia."
        )

        for comando in ("status", "diff", "push", "pull", "status"):
            self.ejecutar(comando)

        for pagina in (raiz / "wiki").rglob("*.md"):
            texto = pagina.read_text(encoding="utf-8")
            for marcador in ("<<<<<<<", "=======", ">>>>>>>"):
                self.assertNotIn(marcador, texto, f"{pagina} tras los comandos")


class Diff(Caso):
    def test_ensena_el_diff_de_lo_mio_y_el_de_outline_contra_la_base(self):
        self.montar_conflicto()

        salida = self.salida("diff")

        self.assertIn("+Lo que escribí yo.", salida)
        self.assertIn("+Lo que escribió mi socia.", salida)
        self.assertIn("wiki/diagnostico.md", salida)

    def test_distingue_de_quien_es_cada_diff(self):
        self.montar_conflicto()

        salida = self.salida("diff")

        mio = salida.index("+Lo que escribí yo.")
        suyo = salida.index("+Lo que escribió mi socia.")
        self.assertLess(salida.index("base -> local"), mio)
        self.assertLess(mio, salida.index("base -> remoto"))
        self.assertLess(salida.index("base -> remoto"), suyo)

    def test_sigue_a_una_pagina_que_he_arrastrado_a_otra_carpeta(self):
        raiz = self.montar_conflicto()
        self.mover_fichero(raiz, "diagnostico.md", "albaran/diagnostico.md")

        salida = self.salida("diff")

        self.assertIn("wiki/albaran/diagnostico.md", salida)
        self.assertIn("+Lo que escribí yo.", salida)

    def test_sin_conflictos_no_ensena_nada(self):
        self.proyecto("Taller")
        self.ejecutar("pull")

        salida = self.salida("diff")

        self.assertNotIn("@@", salida)
        self.assertIn("sin conflictos", salida)

    def test_una_pagina_concreta_se_puede_pedir_por_su_titulo(self):
        self.montar_conflicto()

        salida = self.salida("diff", "Albarán")

        self.assertIn("wiki/albaran.md", salida)
        self.assertNotIn("diagnostico.md", salida)

    def test_una_pagina_movida_tambien_se_puede_pedir_por_su_titulo(self):
        raiz = self.montar_conflicto()
        self.mover_fichero(raiz, "diagnostico.md", "albaran/diagnostico.md")

        salida = self.salida("diff", "Diagnóstico")

        self.assertIn("wiki/albaran/diagnostico.md", salida)
        self.assertIn("+Lo que escribí yo.", salida)


class Resolver(Caso):
    def test_marcar_resuelto_adelanta_la_base_al_remoto(self):
        raiz = self.montar_conflicto()

        self.ejecutar("resolve", "Diagnóstico")

        self.assertIn("Lo que escribió mi socia.", self.base(raiz, "diagnostico.md"))
        salida = self.salida("status")
        self.assertIn("0 en conflicto", salida)
        self.assertIn("1 sucia", salida)

    def test_marcar_resuelto_no_toca_el_fichero_local(self):
        raiz = self.montar_conflicto()
        mio = self.pagina(raiz, "diagnostico.md")

        self.ejecutar("resolve", "Diagnóstico")

        self.assertEqual(self.pagina(raiz, "diagnostico.md"), mio)

    def test_resuelto_el_conflicto_el_push_sube_mi_version(self):
        raiz = self.montar_conflicto()

        self.ejecutar("resolve", "Diagnóstico")
        self.ejecutar("push")

        identificador = self.identificador(raiz, "diagnostico.md")
        texto = self.transporte.documentos[identificador]["text"]
        self.assertIn("Lo que escribí yo.", texto)
        self.assertNotIn("Lo que escribió mi socia.", texto)

    def test_una_pagina_movida_se_resuelve_por_su_titulo_y_dice_donde_esta(self):
        raiz = self.montar_conflicto()
        self.mover_fichero(raiz, "diagnostico.md", "albaran/diagnostico.md")

        salida = self.salida("resolve", "Diagnóstico")

        self.assertIn("wiki/albaran/diagnostico.md", salida)
        # La base se queda en la ruta vieja, que es contra la que compara el push siguiente.
        self.assertIn("Lo que escribió mi socia.", self.base(raiz, "diagnostico.md"))

    def test_resuelta_asi_el_push_sube_mi_version_y_ademas_la_mueve(self):
        raiz = self.montar_conflicto()
        albaran = self.identificador(raiz, "albaran.md")
        self.mover_fichero(raiz, "diagnostico.md", "albaran/diagnostico.md")

        self.ejecutar("resolve", "Diagnóstico")
        self.ejecutar("push")

        diagnostico = self.identificador(raiz, "albaran/diagnostico.md")
        self.assertIn("Lo que escribí yo.", self.transporte.documentos[diagnostico]["text"])
        self.assertEqual(self.transporte.documentos[diagnostico]["parentDocumentId"], albaran)

    def test_sin_decir_que_pagina_falla(self):
        self.montar_conflicto()

        self.ejecutar_fallando("resolve")


class Configuracion(Caso):
    def test_sin_outline_url_falla_y_lo_dice(self):
        self.proyecto("Taller")
        del os.environ["OUTLINE_URL"]

        self.assertIn("OUTLINE_URL", self.ejecutar_fallando("pull"))

    def test_sin_token_falla_y_lo_dice(self):
        self.proyecto("Taller")
        del os.environ["OUTLINE_API_TOKEN"]

        self.assertIn("OUTLINE_API_TOKEN", self.ejecutar_fallando("pull"))


if __name__ == "__main__":
    unittest.main()
