# outline-skill

Un espejo local de una wiki de [Outline](https://www.getoutline.com/) y la skill que lo usa,
para leer y escribir la documentación del proyecto que tengas delante sin salirte de él.

`outline pull` baja la colección que le corresponde al proyecto: la que se llama igual que el
directorio raíz, comparando los dos nombres sin acentos, en minúsculas y con los separadores
unificados. Si ninguna coincide, falla y te lista las que hay. Nunca baja la wiki entera por su
cuenta; eso lo pides con `--all`.

Solo usa la biblioteca estándar de Python, así que clonar y ejecutar funciona sin instalar nada.

## Puesta en marcha

Clona el repositorio donde viven las skills de usuario, para que esté disponible en cualquier
sesión y sea cual sea el directorio de trabajo:

```
git clone https://github.com/Aleexc12/outline-skill %USERPROFILE%\.claude\skills\wiki
```

Pon las dos variables en el entorno de usuario. En Windows, desde PowerShell y nunca con `setx`,
que trunca el `PATH` a 1024 caracteres sin avisar:

```powershell
[Environment]::SetEnvironmentVariable('OUTLINE_URL', 'https://wiki.ejemplo.com', 'User')
[Environment]::SetEnvironmentVariable('OUTLINE_API_TOKEN', '<tu token>', 'User')
```

El token se genera en Settings → API Keys de tu instancia.

Enlaza los dos lanzadores desde una carpeta que esté en el `PATH`. `outline.cmd` es para `cmd` y
PowerShell, y `outline` sin extensión es para Git Bash:

```powershell
$bin = "$env:USERPROFILE\bin"
$home_ = "$env:USERPROFILE\.claude\skills\wiki"
New-Item -ItemType SymbolicLink -Path "$bin\outline.cmd" -Target "$home_\outline.cmd"
New-Item -ItemType SymbolicLink -Path "$bin\outline"     -Target "$home_\outline"
```

Los lanzadores buscan el script en `%USERPROFILE%\.claude\skills\wiki`. Si lo clonaste en otro
sitio, apunta `OUTLINE_HOME` ahí.

## Uso

```
outline pull          # baja la colección que se llama como el proyecto
outline pull --all    # baja todas las colecciones
outline status        # qué has tocado tú y qué se ha movido en Outline
outline check "..."   # compara la revisión de una página con la del remoto
```

El `pull` no borra la carpeta.
Recorre las páginas una a una, actualiza las que no has tocado y deja intactas las que sí,
listándolas al final.
Una página que desapareció de Outline se borra en local solo si estaba limpia.
Renombrarla en Outline la mueve de ruta aquí, pero si en la ruta nueva ya tienes un fichero con
cambios tuyos, la página se queda donde estaba y el `pull` lo dice en vez de escribir encima.

Cada página tiene tres versiones: la local, en `wiki/`; la base, en `.outline/base/`, que es
lo que Outline dio en el último `pull`; y la remota.
De compararlas salen cuatro estados.

| local vs base | remoto vs base | Estado | Qué hace el `pull` |
|---|---|---|---|
| igual | igual | limpia | nada |
| igual | cambió | remota adelantada | actualiza local y base |
| cambió | igual | sucia | no la toca |
| cambió | cambió | conflicto | no la toca |

Editar en local todavía no publica, porque `push` llega después.
Mientras tanto, una página editada a mano se queda fuera de las actualizaciones hasta que su
cambio llegue a Outline.

## Qué queda en el disco

`wiki/` son las páginas y nada más.
Cada `.md` empieza con su identidad, y con nada más: cualquier otro dato se queda viejo en
cuanto alguien edita.

```
---
outline_id: 3fc2b126-f371-45b9-a9ff-a4ae45a8328a
---

# Título de la página
```

El título es ese encabezado de nivel 1, así que no vive en dos sitios.

Junto a `wiki/` está `.outline/`, el estado de la máquina.
Se reconstruye con un `pull`, así que conviene añadirlo al `.gitignore` del proyecto:

- `manifest.json`, con la ruta, la revisión del último `pull` y la colección de cada página,
  más una marca de `pull` completo que solo se pone si la pasada terminó sin errores.
- `base/`, la copia en la sombra: lo que Outline dio en el último `pull`, con la misma
  estructura de carpetas y sin frontmatter, para comparar cuerpo contra cuerpo.
- `index.md`, el árbol completo en el orden real de la barra lateral de Outline, que el árbol
  de directorios no guarda.

## Tests

```
py -m unittest
```

Los tests sustituyen el transporte HTTP por un doble que sirve un árbol de wiki guionizado y
registra las peticiones. Todo lo demás pasa por la línea de comandos de verdad.
