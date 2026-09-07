"""Tests de outline.py.

La única costura es el transporte HTTP: el método que hace el POST contra la API se
sustituye por un doble que sirve un árbol de wiki guionizado y registra las peticiones.
La raíz del proyecto es un directorio temporal. Todo lo demás pasa por la línea de
comandos de verdad.
"""

import contextlib
import io
import os
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
        }
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
