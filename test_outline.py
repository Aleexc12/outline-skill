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
        self.inaccesibles = set()
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
                [
                    d for d in self.documentos.values()
                    if d["collectionId"] == coleccion and d["id"] not in self.inaccesibles
                ]
            )
        if endpoint == "collections.documents":
            return {"data": self.arboles[payload["id"]]}
        if endpoint == "documents.info":
            if payload["id"] in self.inaccesibles:
                return {"data": None}
            return {"data": self.documentos[payload["id"]]}
        raise AssertionError(f"endpoint no guionizado: {endpoint}")

    def editar(self, documento_id, texto):
        """Un socio edita esa página en Outline, lo que adelanta su revisión."""
        documento = self.documentos[documento_id]
        documento["text"] = texto
        documento["revision"] += 1

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
        self.transporte.documentos[identificador]["title"] = titulo
        for coleccion, arbol in self.transporte.arboles.items():
            self.transporte.arboles[coleccion] = self._retitular(arbol, identificador, titulo)
        return identificador

    def _retitular(self, nodos, identificador, titulo):
        return [
            {
                **nodo,
                "title": titulo if nodo["id"] == identificador else nodo["title"],
                "children": self._retitular(nodo["children"], identificador, titulo),
            }
            for nodo in nodos
        ]

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
