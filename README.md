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
outline check "..."   # compara revisiones sin escribir nada
```

## Tests

```
py -m unittest
```

Los tests sustituyen el transporte HTTP por un doble que sirve un árbol de wiki guionizado y
registra las peticiones. Todo lo demás pasa por la línea de comandos de verdad.
