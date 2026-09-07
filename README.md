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
outline push          # sube lo que has escrito, y crea, mueve y renombra
outline push --yes    # además confirma los borrados
outline diff "..."    # los dos lados de un conflicto, contra la base
outline resolve "..." # da un conflicto por resuelto
outline check "..."   # compara la revisión de una página con la del remoto
```

El `pull` no borra la carpeta.
Recorre las páginas una a una, actualiza las que no has tocado y deja intactas las que sí,
listándolas al final.
Una página que desapareció de Outline se borra en local solo si estaba limpia.
Renombrarla en Outline la mueve de ruta aquí, pero si en la ruta nueva ya tienes un fichero con
cambios tuyos, la página se queda donde estaba y el `pull` lo dice en vez de escribir encima.

Cada página tiene tres versiones: la local, en `wiki/`; la base, en `.outline/base/`, que es
lo último que Outline te dio; y la remota.
De compararlas salen cuatro estados.

| local vs base | remoto vs base | Estado | `pull` | `push` |
|---|---|---|---|---|
| igual | igual | limpia | nada | nada |
| igual | cambió | remota adelantada | actualiza local y base | nada |
| cambió | igual | sucia | no la toca | sube y adelanta la base |
| cambió | cambió | conflicto | no la toca | se niega |

Escribir es editar el Markdown y hacer `push`.
Sube solo las páginas sucias, y de cada una comprueba la revisión remota justo antes de
escribirla.
Si Outline se ha movido desde tu último `pull`, esa página no se toca.
Se acepta la ventana de milisegundos entre comprobar y escribir, porque enfrente hay personas
escribiendo a ritmo humano y Outline guarda el historial de revisiones de cada página.
Si el último `pull` no llegó a terminar, el `push` avisa antes de nada, porque entonces la base
puede estar a medias.

Una página nueva es un `.md` nuevo en la carpeta que le toca.
Sin `outline_id`, el `push` deduce la colección y la página padre de su ruta en el disco, la
crea al final de su nivel y escribe el identificador devuelto en el frontmatter del fichero.
Así la jerarquía del disco y la de Outline son la misma cosa, y el título sale del encabezado
de nivel 1, que viaja aparte del cuerpo para que no salga duplicado.

Mover una página es arrastrar su `.md` a otra carpeta, y el `push` la reanida en Outline sin
perder historial, comentarios ni enlaces.
Si Outline tiene texto que no has visto, la mueve igual, porque mover no pisa nada, pero se
queda con la revisión de antes y te dice que le pases un `pull`.
Un documento con hijas ocupa dos entradas, su propio `.md` y una carpeta hermana con el mismo
nombre, así que hay que llevarse las dos.
Renombrarla es cambiar su encabezado de nivel 1: el nombre del fichero sale de ese título, y lo
ajusta el `pull` siguiente.

Borrarla es borrar su fichero, y el `push` la manda a la papelera de Outline, de donde se
recupera.
Como es la operación que más duele si te equivocas, lleva cuatro barreras.
El borrado se calcula como "estaba en el manifiesto del último `pull` y ya no está en el disco",
nunca como "está en Outline y no en el disco", así que ni una colección fuera del ámbito ni una
página que otra persona creó después de ese `pull` cuentan como borradas.
Si el último `pull` no dejó la marca de completo, el `push` no borra, porque un fichero que
falta puede ser un fallo de red.
Antes de borrar lista las páginas afectadas y pide confirmación, y sin nadie al teclado no borra
nada, así que hay que repetir con `outline push --yes`.
Y la llamada va siempre a la papelera, nunca es permanente.

Aparte de esas cuatro, Outline se lleva la rama entera al borrar, así que borrar una página con
hijas es borrar también su carpeta.
Con hijas vivas en el disco el `push` no la borra y lo dice, porque si no borraría en Outline
páginas que nadie pidió borrar.

Ante un conflicto, la herramienta nunca escribe marcadores dentro del fichero.
En Markdown no hay compilador que avise, y un `<<<<<<<` que se escape acaba publicado en la
wiki.
En su lugar, `outline diff` enseña dos diffs contra la base, el tuyo y el de Outline.
Deja el fichero como lo quieras, márcalo con `outline resolve`, que adelanta la base al remoto
de ahora mismo, y súbelo con `push`.

## Qué queda en el disco

`wiki/` son las páginas y nada más.
Cada `.md` empieza con su identidad y con nada más, porque cualquier otro dato se queda
viejo en cuanto alguien edita.

```
---
outline_id: 3fc2b126-f371-45b9-a9ff-a4ae45a8328a
---

# Título de la página
```

El título es ese encabezado de nivel 1, así que no vive en dos sitios.

Junto a `wiki/` está `.outline/`, el estado de la máquina.
Se reconstruye con un `pull`, así que conviene añadirlo al `.gitignore` del proyecto:

- `manifest.json`, con la ruta, la revisión que viste por última vez y la colección de cada
  página, más una marca de `pull` completo que solo se pone si la pasada terminó sin errores.
- `base/`, la copia en la sombra de lo último que Outline dio, con la misma
  estructura de carpetas y sin frontmatter, para comparar cuerpo contra cuerpo.
- `index.md`, el árbol completo en el orden real de la barra lateral de Outline, que el árbol
  de directorios no guarda.

## Tests

```
py -m unittest
```

Los tests sustituyen el transporte HTTP por un doble que sirve un árbol de wiki guionizado y
registra las peticiones. Todo lo demás pasa por la línea de comandos de verdad.
