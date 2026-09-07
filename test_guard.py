"""Qué comandos de shell deja pasar el hook que protege `.outline/` y cuáles no."""

import unittest

from guard import offence


class Bloquea(unittest.TestCase):
    def assertBlocked(self, command):
        self.assertIsNotNone(offence(command), command)

    def test_borrar_la_carpeta_entera(self):
        self.assertBlocked("rm -rf .outline")

    def test_borrar_el_manifiesto(self):
        self.assertBlocked("rm .outline/manifest.json")

    def test_redirigir_encima_del_manifiesto(self):
        self.assertBlocked('echo "{}" > .outline/manifest.json')

    def test_anadir_al_final_de_la_base(self):
        self.assertBlocked("cat wiki/a.md >> .outline/base/a.md")

    def test_editar_el_manifiesto_en_el_sitio(self):
        self.assertBlocked("sed -i s/10/11/ .outline/manifest.json")

    def test_entrar_en_la_carpeta_y_borrar_desde_dentro(self):
        self.assertBlocked("cd .outline && rm -rf base")

    def test_escribir_al_final_de_una_tuberia(self):
        self.assertBlocked("jq . a.json | tee .outline/manifest.json")

    def test_un_interprete_que_puede_hacer_cualquier_cosa(self):
        self.assertBlocked('python -c "open(\'.outline/manifest.json\',\'w\')"')

    def test_los_cmdlets_de_powershell(self):
        self.assertBlocked("Remove-Item .outline -Recurse -Force")
        self.assertBlocked("Get-Content a | Set-Content .outline/manifest.json")

    def test_una_ruta_absoluta_tambien(self):
        self.assertBlocked("rm -rf /c/Proyectos/DocsProyectos/.outline/base")

    def test_una_redireccion_dentro_de_un_heredoc_no_es_texto(self):
        self.assertBlocked("cat > .outline/manifest.json <<'EOF'\n{}\nEOF")


class Deja(unittest.TestCase):
    def assertAllowed(self, command):
        self.assertIsNone(offence(command), command)

    def test_leer_el_indice(self):
        self.assertAllowed("cat .outline/index.md")

    def test_filtrar_el_manifiesto(self):
        self.assertAllowed("jq .documents .outline/manifest.json")

    def test_buscar_dentro_del_estado(self):
        self.assertAllowed("grep -n titulo .outline/index.md")

    def test_leer_y_redirigir_fuera_del_estado(self):
        self.assertAllowed("grep foo .outline/index.md > /tmp/salida")

    def test_listar_la_copia_en_la_sombra(self):
        self.assertAllowed("ls -la .outline/base | head")

    def test_los_cmdlets_de_lectura_de_powershell(self):
        self.assertAllowed("Get-Content .outline/index.md -TotalCount 20")

    def test_la_herramienta_escribiendo_su_propio_estado(self):
        self.assertAllowed("outline pull --all")

    def test_borrar_una_pagina_del_espejo(self):
        self.assertAllowed("rm wiki/flota/reuniones/03-08.md")

    def test_un_heredoc_que_solo_habla_del_estado(self):
        self.assertAllowed(
            "cat > CLAUDE.md <<'EOF'\n"
            "`.outline/` es estado de la máquina y no se toca.\n"
            "Se reconstruye con `outline pull`.\n"
            "EOF"
        )

    def test_un_comando_que_no_lo_menciona(self):
        self.assertAllowed("rm -rf wiki && git checkout -- wiki")


if __name__ == "__main__":
    unittest.main()
