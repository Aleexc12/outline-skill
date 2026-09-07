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

1. **Editar `wiki/` a mano no publica nada todavía.**
   El `pull` ya no borra la carpeta, así que un cambio local sobrevive.
   Pero `push` aún no existe: para que ese cambio llegue a Outline hay que escribirlo por API,
   y mientras tanto el `pull` se salta esa página y su copia se queda vieja.
2. **Leer siempre en local, escribir siempre por API.**
   Un documento por `documents.info` cuesta de 4 a 6 veces más contexto que su `.md`.
3. **Antes de escribir: `status`, o `check` de esa página, y `pull` si está desactualizada.**
   Sin la copia fresca, el `findText` de un `patch` no coincide con el texto real.
   Para una página nueva no hay nada que comprobar, así que ahí se baja directamente:
   hace falta para ver el árbol actual, elegir dónde colocarla y no duplicar algo que ya existe.
4. **Preferir `patch` y `append` frente a `replace`.**
   Una reescritura completa pisa el trabajo de quien esté editando otra sección.
5. **Filtrar toda respuesta de la API con `jq`.**
   Las respuestas traen el documento entero más su árbol JSON del editor.
6. **Construir el cuerpo con `jq -n --arg` y enviarlo por stdin con `--data-binary @-`.**
   Pasar el JSON dentro de `-d "..."` corrompe las tildes y las eñes: en Windows los
   argumentos de proceso pasan por la página de códigos del sistema y el UTF-8 se pierde.
   El título llega a Outline con `�` en lugar de cada acento, y como Outline es la
   fuente de verdad, el destrozo es permanente.

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

## Saber en qué estado estás

Cada página tiene tres versiones: la local, en `wiki/`; la base, en `.outline/base/`, que es lo
que Outline dio en el último `pull`; y la remota.
De compararlas salen cuatro estados.

```bash
outline status        # la colección del proyecto
outline status --all  # todas
```

| local vs base | remoto vs base | Estado | Qué hace el `pull` |
|---|---|---|---|
| igual | igual | limpia | nada |
| igual | cambió | remota adelantada | actualiza local y base |
| cambió | igual | sucia | no la toca |
| cambió | cambió | conflicto | no la toca |

`status` lista las que no están limpias, y aparte los ficheros que aún no existen en Outline.
Para una página suelta, `check` compara su revisión con la del remoto y sale con código 1 si
hay que sincronizar.
Acepta id, ruta o un trozo del título.

```bash
outline check "nombre de la pagina"
```

## Escribir

El token y la dirección viven en las variables de entorno de usuario, así que ya están puestos:

```bash
TOKEN=$OUTLINE_API_TOKEN
API=$OUTLINE_URL/api
```

**Página nueva.**
Dónde colocarla se decide leyendo `.outline/index.md`, que trae el árbol entero con títulos
y jerarquía: basta para elegir colección y página padre sin preguntar.
Lo normal es colgarla de la página con la que comparte tema, y dejarla en la raíz de la
colección solo cuando abre un tema nuevo.
Crear el `.md` en local no crea la página: se decide leyendo el espejo local y se escribe
en Outline, que es lo que el siguiente `pull` refleja.

Sin `publish` la página queda como borrador y no la ve nadie.
El `collectionId` no está en el manifiesto, que solo guarda el nombre de la colección,
y una colección vacía no aparece ahí en absoluto. Se pide a la API:

```bash
jq -n '{limit: 100}' \
| curl -s -X POST $API/collections.list \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    --data-binary @- \
| jq -c '.data[] | {id, name, permission}'
```

```bash
jq -n --arg title "Título" --arg collection "<id>" \
  --arg text "Entradilla.

## Apartado

Contenido." \
  '{title: $title, text: $text, collectionId: $collection, publish: true}' \
| curl -s -X POST $API/documents.create \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    --data-binary @- \
| jq -c '{id: .data.id, url: .data.url}'
```

El `text` no debe empezar por un encabezado de nivel 1: el título es un campo aparte y saldría duplicado.
Los encabezados del cuerpo empiezan en `##`.

**Página anidada.**
Añadir `parentDocumentId` para que nazca colgando de otra página, con su id sacado de `.outline/index.md`.
Cualquier documento puede ser padre, y la profundidad no está limitada.
Las colecciones, en cambio, no se anidan entre sí: la jerarquía se construye siempre con documentos.

```bash
jq -n --arg title "Título" --arg collection "<id>" --arg parent "<id-padre>" \
  '{title: $title, text: "Contenido.", collectionId: $collection, parentDocumentId: $parent, publish: true}' \
| curl -s -X POST $API/documents.create \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    --data-binary @- \
| jq -c '{id: .data.id, url: .data.url}'
```

Para reanidar una página que ya existe está `documents.move`, en `endpoints.md`.

En `wiki/` un documento con hijos genera dos entradas: su propio `.md` y una carpeta hermana
con el mismo nombre donde van los hijos.

**Añadir al final.**

```bash
jq -n --arg id "<id>" --arg text "
## Sección nueva

Contenido.
" \
  '{id: $id, text: $text, editMode: "append"}' \
| curl -s -X POST $API/documents.update \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    --data-binary @- \
| jq -c '{id: .data.id, revision: .data.revision}'
```

**Sustituir un fragmento.**
`findText` es obligatorio con `patch` y debe coincidir exactamente con el texto actual.

```bash
jq -n --arg id "<id>" --arg viejo "texto viejo" --arg nuevo "texto nuevo" \
  '{id: $id, editMode: "patch", findText: $viejo, text: $nuevo}' \
| curl -s -X POST $API/documents.update \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    --data-binary @- \
| jq -c '{id: .data.id, revision: .data.revision}'
```

Después de escribir, hacer `outline pull` otra vez para que la copia local recoja la revisión nueva.

## Más endpoints

`endpoints.md`, en esta misma carpeta, tiene los parámetros de búsqueda, mover, borrar,
subir imágenes, crear colecciones e invitar gente.

## Avisos

Nunca escribir directamente en el PostgreSQL de Outline: rompe el índice de búsqueda,
el historial de revisiones y el estado del editor colaborativo.

Un cambio por API sobre una página que alguien tiene abierta en el editor colaborativo
puede necesitar un refresco de la pestaña para verse.

El `text` de un `patch` se parsea como Markdown antes de insertarse, también cuando cae dentro
de un bloque de código, donde el formato no existe y se pierde: enviar `**Después.**` deja
`Después.` y escaparlo deja las barras a la vista. Para tocar dentro de un bloque de código
hay que reemplazarlo entero, con sus tres comillas de apertura y cierre dentro del `findText`.

No conectar el servidor MCP de Outline.
Sus 19 definiciones de herramientas ocuparían contexto en todas las sesiones, use la wiki o no,
y sus lecturas devuelven el mismo JSON verboso que la API.
