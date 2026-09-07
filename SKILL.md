---
name: wiki
description: Leer y escribir en la wiki de Outline del equipo. Usar siempre que haya que consultar documentación del proyecto, redactar documentación nueva o modificar una página existente.
---

# Wiki del equipo en Outline

La wiki vive en la instancia que apunte `OUTLINE_URL` y es la única fuente de verdad.
La carpeta `wiki/` del proyecto es una copia local que no se actualiza sola: solo cambia
cuando se ejecuta `outline pull`.

Los comandos se ejecutan desde la raíz del proyecto.

## Reglas

1. **Escribir es editar el Markdown de `wiki/` y hacer `outline push`.**
   Nunca se construye una petición HTTP para cambiar el texto de una página.
   Editando ficheros, los acentos, el formato dentro de los bloques de código y la coincidencia
   exacta del texto dejan de ser fuentes de error.
2. **Leer siempre en local.**
   Un documento por `documents.info` cuesta de 4 a 6 veces más contexto que su `.md`.
3. **Leer `.outline/index.md` antes de crear una página.**
   Trae el árbol entero con títulos, jerarquía e identificadores, que es lo que hace falta para
   elegir dónde colgarla sin preguntar y sin duplicar algo que ya existe.
4. **Filtrar toda respuesta de la API con `jq`.**
   Las respuestas traen el documento entero más su árbol JSON del editor.

## Leer

```bash
outline pull        # baja la colección que se llama como el proyecto
outline pull --all  # baja todas las colecciones
```

Sin `--all`, la colección sale del nombre del directorio raíz del proyecto, comparado con el de
cada colección sin acentos, en minúsculas y con los separadores unificados.
Si ninguna coincide, el comando falla y lista las colecciones que hay: no cae de vuelta a la wiki
entera. Con una sola colección en ámbito, las páginas cuelgan directamente de `wiki/`; con `--all`,
el primer segmento de la ruta es la colección.

Después, leer los `.md` de `wiki/` con las herramientas de ficheros normales.
Cada `.md` empieza con su `outline_id` en el frontmatter, que es el id que piden los endpoints.
`.outline/index.md` lista las páginas en el orden real de Outline, con su id al lado.

Junto a `wiki/` queda `.outline/`, que es estado de la máquina y no se edita a mano: el
manifiesto con la revisión de cada página, la copia en la sombra del último `pull` en `base/`
y ese índice.
El `pull` lo actualiza a la vez que las páginas.
Un hook corta cualquier comando que escriba ahí, así que si uno se bloquea es esto: rehaz el
estado con `outline pull` en vez de arreglarlo a mano.

## Saber en qué estado estás

Cada página tiene tres versiones: la local, en `wiki/`; la base, en `.outline/base/`, que es lo
que Outline dio en el último `pull`; y la remota.
De compararlas salen cuatro estados.

```bash
outline status        # la colección del proyecto
outline status --all  # todas
```

| local vs base | remoto vs base | Estado | `pull` | `push` |
|---|---|---|---|---|
| igual | igual | limpia | nada | nada |
| igual | cambió | remota adelantada | actualiza local y base | nada |
| cambió | igual | sucia | no la toca | sube y adelanta la base y el fichero |
| cambió | cambió | conflicto | no la toca | se niega |

`status` lista las que no están limpias, y aparte los ficheros que aún no existen en Outline.
Para una página suelta, `check` compara su revisión con la del remoto y sale con código 1 si
hay que sincronizar.
Acepta id, ruta o un trozo del título.

```bash
outline check "nombre de la pagina"
```

## Escribir

Cada operación es un cambio en el disco, y `outline push` la lleva a Outline.

| En el disco | En Outline |
|---|---|
| editar el cuerpo de un `.md` | cambia el texto de la página |
| cambiar su encabezado de nivel 1 | la renombra |
| crear un `.md` en la carpeta que le toca | crea la página, colgada de donde diga la ruta |
| arrastrar el `.md` a otra carpeta | la reanida, sin perder historial, comentarios ni enlaces |
| borrar el `.md` | la manda a la papelera |

```bash
outline push             # sube lo tocado, crea, mueve y renombra
outline push --yes       # además confirma los borrados
outline diff "pagina"    # los dos diffs contra la base, el tuyo y el de Outline
outline resolve "pagina" # da el conflicto por resuelto, sin tocar tu fichero
```

El cuerpo que se manda no lleva el frontmatter ni repite el título.
El identificador de una página recién creada vuelve al frontmatter del fichero local.
El `push` nunca escribe marcadores de conflicto dentro de un fichero.

Outline reescribe el Markdown al guardarlo: junta los saltos de línea sueltos dentro de un
párrafo y normaliza el separador de las tablas.
El `push` deja el fichero local con lo que Outline devuelve, y lista las páginas reformateadas.
Sin eso, una página escrita con una frase por línea se subiría en todas las pasadas y el `pull`
dejaría de traer lo que otros escriban en ella.

**Página nueva.**
Un `.md` en la carpeta que le toca.
La colección y la página madre salen de su ruta, así que la jerarquía del disco y la de Outline
son la misma cosa: `wiki/chapa/pintura.md` cuelga de `wiki/chapa.md`.
Dónde colocarla se decide leyendo `.outline/index.md`.
Lo normal es colgarla de la página con la que comparte tema, y dejarla en la raíz de la colección
solo cuando abre un tema nuevo.
Nace al final de su nivel.

**Mover.**
Arrastrar el fichero a otra carpeta.
Un documento con hijas ocupa dos entradas, su propio `.md` y una carpeta hermana con el mismo
nombre donde van las hijas, así que hay que llevarse las dos.
Si Outline tiene texto que no has visto, la mueve igual y te dice que le pases un `pull`.

**Renombrar.**
Cambiar el encabezado de nivel 1, que es el único sitio donde vive el título.
El nombre del fichero sale de ese título, así que el `pull` siguiente renombra el fichero.

**Borrar.**
Borrar el fichero, y el `push` manda la página a la papelera de Outline, de donde se recupera.
Antes lista las páginas que va a borrar y pide confirmación.
Sin nadie al teclado no borra nada, así que hay que repetir con `outline push --yes`.
El borrado se calcula como "estaba en el manifiesto del último `pull` y ya no está en el disco",
así que ni una colección fuera del ámbito ni una página que otra persona creó después de ese
`pull` cuentan nunca como borradas.
Si el último `pull` no terminó tampoco borra, porque un fichero que falta puede ser un fallo de
red y no un borrado tuyo.
Outline se lleva a la papelera el árbol entero, así que borrar una página con hijas es borrar
también su carpeta; con hijas vivas en el disco, el `push` no la borra y lo dice.

**Conflictos.**
Si la página se movió en Outline desde tu último `pull`, el `push` no la toca y lo dice.
`outline diff` enseña los dos lados contra la base y `outline resolve` da el conflicto por
resuelto, adelantando la base al remoto de ahora mismo y sin tocar tu fichero.

## Más endpoints

`endpoints.md`, en esta misma carpeta, tiene lo que no es el texto de una página: buscar, mover
y borrar por API, subir imágenes, crear colecciones e invitar gente.

## Avisos

Nunca escribir directamente en el PostgreSQL de Outline: rompe el índice de búsqueda,
el historial de revisiones y el estado del editor colaborativo.

Un cambio por API sobre una página que alguien tiene abierta en el editor colaborativo
puede necesitar un refresco de la pestaña para verse.

No conectar el servidor MCP de Outline.
Sus 19 definiciones de herramientas ocuparían contexto en todas las sesiones, use la wiki o no,
y sus lecturas devuelven el mismo JSON verboso que la API.
