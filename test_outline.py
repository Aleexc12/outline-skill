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


class Transporte:
    """Doble del POST: sirve el árbol guionizado y registra lo que se le pide."""

    def __init__(self, arbol=ARBOL):
        self.peticiones = []
        self.colecciones = []
        self.documentos = {}
        self.arboles = {}
        self.romper = None
        for indice, (nombre, nodos) in enumerate(arbol.items(), start=1):
            coleccion = f"col-{indice}"
            self.colecciones.append({"id": coleccion, "name": nombre})
            self.arboles[coleccion] = self._construir(nodos, coleccion)

    def _construir(self, nodos, coleccion):
        salida = []
        for nodo in nodos:
            documento_id = f"doc-{len(self.documentos) + 1:03d}"
            self.documentos[documento_id] = {
                "id": documento_id,
                "title": nodo["title"],
                "text": nodo["text"],
                "revision": 1,
                "updatedAt": "2026-09-07T00:00:00.000Z",
                "urlId": documento_id,
                "url": f"/doc/{documento_id}",
                "collectionId": coleccion,
            }
            salida.append(
                {
                    "id": documento_id,
                    "title": nodo["title"],
                    "children": self._construir(nodo.get("children", []), coleccion),
                }
            )
        return salida

    def post(self, endpoint, payload):
        self.peticiones.append((endpoint, payload))
        if endpoint == self.romper:
            raise ConnectionError("la red se cayó a media pasada")
        if endpoint == "collections.list":
            return self._pagina(self.colecciones)
        if endpoint == "documents.list":
            coleccion = payload.get("collectionId")
            return self._pagina(
                [d for d in self.documentos.values() if d["collectionId"] == coleccion]
            )
        if endpoint == "collections.documents":
            return {"data": self.arboles[payload["id"]]}
        if endpoint == "documents.info":
            return {"data": self.documentos[payload["id"]]}
        raise AssertionError(f"endpoint no guionizado: {endpoint}")

    def colecciones_pedidas(self):
        return [p["collectionId"] for e, p in self.peticiones if e == "documents.list"]

    @staticmethod
    def _pagina(items):
        return {"data": items, "pagination": {"total": len(items)}}


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

    def proyecto(self, nombre):
        raiz = self.temporal / nombre
        (raiz / ".git").mkdir(parents=True)
        os.chdir(raiz)
        return raiz

    def ejecutar(self, *argv):
        with contextlib.redirect_stdout(io.StringIO()):
            return outline.main(list(argv))

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
            },
        )

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

    def test_sin_manifiesto_pide_un_pull(self):
        self.proyecto("Taller")

        self.assertIn("outline pull", self.ejecutar_fallando("check"))


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
