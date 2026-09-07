# Endpoints de la API de Outline

Referencia de los endpoints útiles para esta wiki, más allá de los del `SKILL.md`.

Toda la API es RPC uniforme: siempre `POST`, siempre JSON en el cuerpo, siempre con
la cabecera `Authorization: Bearer $TOKEN`.
Toda respuesta tiene la forma `{ok, status, data, policies, pagination}`.

Los marcados como **probado** se han ejecutado contra la instancia real.
Los marcados como **sin probar** salen de la especificación oficial y conviene confirmarlos
la primera vez que se usen.

El cuerpo se construye con `jq -n --arg` y se envía por stdin con `--data-binary @-`,
nunca dentro de `-d "..."`, que destroza los acentos. La regla 6 del `SKILL.md` lo explica.

## Buscar sin bajarse la wiki

`documents.search` **probado**

| Campo | Notas |
|---|---|
| `query` | obligatorio |
| `limit`, `offset` | paginación, máximo 100 |
| `collectionId` | restringe a una colección |
| `statusFilter` | array, p. ej. `["published"]` |

Devuelve `data` como array de `{ranking, context, document}`.
El `context` es el fragmento con la coincidencia, y es lo único que suele hacer falta.
Ojo: `document` incluye el `text` entero, así que sin filtrar es carísimo en contexto.

```bash
jq -n --arg q "despliegue" '{query: $q, limit: 5}' \
| curl -s -X POST $API/documents.search \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    --data-binary @- \
| jq -c '.data[] | {id: .document.id, title: .document.title, context}'
```

Para buscar solo en la copia local, `grep` sobre `wiki/` es gratis y suele bastar.
`documents.search` sirve cuando importa el ranking o la copia local puede estar vieja.

## Mover y reorganizar

`documents.move` **probado**

| Campo | Notas |
|---|---|
| `id` | obligatorio |
| `collectionId` | colección de destino |
| `parentDocumentId` | para colgarlo de otra página, `null` para dejarlo en la raíz |
| `index` | posición dentro del nivel |

Hay que pasar al menos `collectionId` o `parentDocumentId`.
Devuelve los documentos y colecciones afectados, que es una respuesta grande, así que
conviene filtrarla con `jq`.
El documento que devuelve ya trae la revisión de después del movimiento.

Tres cosas comprobadas contra la instancia el 2026-09-07, porque el `push` depende de ellas.
`documents.create` no acepta posición, y Outline cuelga la página nueva **al principio** de su
nivel, no al final.
Un `index` mayor que el número de hermanos no falla, se recorta y la deja la última.
Mover sube la revisión del documento aunque el texto no cambie, así que quien mueva algo tiene
que quedarse con la revisión que devuelve el movimiento, no con la de antes.

## Borrar y archivar

`documents.delete` **probado**

| Campo | Notas |
|---|---|
| `id` | obligatorio |
| `permanent` | `false` manda a la papelera, `true` borra sin vuelta atrás |

Sin `permanent` la página se puede recuperar desde la papelera de Outline.
Con `permanent: true` no hay recuperación posible salvo restaurando el backup de la VPS.

`documents.archive` **probado** saca la página de la barra lateral sin borrarla,
y solo necesita `id`.
Es la opción correcta para documentación obsoleta que conviene conservar.
Una página archivada desaparece de `wiki/` en la siguiente sincronización.

## Imágenes y adjuntos

`attachments.create` **probado**

Subir una imagen son dos llamadas: una pide permiso y devuelve un formulario firmado, y otra manda el fichero.

| Campo | Notas |
|---|---|
| `name` | nombre del fichero, es lo que se ve al descargarlo |
| `contentType` | `image/png`, `image/jpeg` |
| `size` | tamaño en bytes, obligatorio y tiene que ser el real |
| `documentId` | opcional, ata el adjunto a esa página |

```bash
IMG=docs/imagenes/figura.png
jq -n --arg name "$(basename $IMG)" --arg ct "image/png" \
      --argjson size "$(stat -c %s "$IMG")" --arg doc "<id-de-la-pagina>" \
  '{name: $name, contentType: $ct, size: $size, documentId: $doc}' \
| curl -s -X POST $API/attachments.create \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    --data-binary @- > att.json
jq -c '{uploadUrl: .data.uploadUrl, id: .data.attachment.id}' att.json
```

En esta instancia `uploadUrl` es `/api/files.create`, porque el almacenamiento es local y no S3.
`data.form` son los campos firmados (`key`, `sig`, `acl`, `contentType`...) que el segundo paso tiene que reenviar tal cual.
El id del adjunto sale ya de esta primera llamada: la subida no devuelve más que `{"success": true}`.

**Los campos del formulario no se pueden montar en la línea de comandos.**
Interpolar `jq` dentro de `-F` mete las comillas dentro del valor y `key` deja de casar con lo que el servidor espera,
que se ve como `validation_error: key: Must be of the form <bucket>/<uuid>/<uuid>/<name>`.
Leerlos en un bucle con separador nulo tampoco sirve: el carácter nulo acaba en la línea de comandos y el harness la rechaza.
Lo que funciona es escribir un fichero de configuración de `curl` y pasarlo con `--config`.

```bash
py - <<'PYEOF'
import json, pathlib
d = json.loads(pathlib.Path("att.json").read_text(encoding="utf-8"))["data"]
lineas = [f'form = "{k}={v}"' for k, v in d["form"].items()]
lineas.append('form = "file=@docs/imagenes/figura.png;type=image/png"')
pathlib.Path("curl.cfg").write_text("\n".join(lineas) + "\n", encoding="utf-8")
PYEOF

curl -s -X POST $API/files.create -H "Authorization: Bearer $TOKEN" --config curl.cfg
```

El Python es el de Windows y no ve el `/tmp` de Git Bash, así que los dos ficheros intermedios van al scratchpad de la sesión y no a `/tmp`.

El tope de subida lo dice el propio formulario, en `maxUploadSize`: 1.000.000 bytes.
Un PNG de 2200x1200 a 200 ppp ronda los 90 KB, así que las figuras del análisis caben de sobra.

**Colocar la imagen en la página** es un `patch` normal sobre el markdown, con la sintaxis propia de Outline:

```markdown
![](/api/attachments.redirect?id=<id-del-adjunto> " =733.3333333333334x400")
```

Esa cadena del final es el tamaño con el que se muestra.
El ancho de columna del editor es 733,33, y el alto hay que calcularlo con la relación de aspecto real del fichero: si no cuadra, Outline deforma la imagen.

`attachments.delete` **sin probar** recibe `id`.
Sustituir una figura por otra deja el adjunto viejo en el servidor sin que nada lo referencie, así que si se acumulan hay que limpiarlos por aquí.

## Colecciones

`collections.create` **sin probar**

| Campo | Notas |
|---|---|
| `name` | obligatorio |
| `description` | texto de portada |
| `permission` | `read`, `read_write`, o `null` para que sea privada |
| `color`, `icon` | apariencia en la barra lateral |

`permission: null` crea una colección privada, visible solo para quien la crea y para
quien se invite después.
Omitir el campo equivale a `null`, así que **hay que escribir `read_write` siempre**
para que la colección sea del equipo. No existe ningún ajuste del espacio de trabajo
que cambie ese comportamiento: se decide colección por colección, y en la interfaz
es el selector de permisos del diálogo de creación.
Una colección privada es invisible incluso para los demás administradores, y sus páginas
no aparecen ni en `collections.list` ni en la sincronización.

`collections.update` **probado** arregla una colección que nació privada.

```bash
jq -n --arg id "<id>" '{id: $id, permission: "read_write"}' \
| curl -s -X POST $API/collections.update \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    --data-binary @- \
| jq -c '{name: .data.name, permission: .data.permission}'
```

`collections.list` y `collections.documents` ya los usa el script de sincronización.
El segundo devuelve el árbol ordenado tal y como se ve en la barra lateral, con `children` anidados.

## Equipo

`users.list` **probado** acepta `query`, `filter` (`all`, `admins`, `suspended`,
`invited`, `viewers`) y paginación.

`users.invite` **sin probar** recibe `invites`, un array de objetos con `name`, `email`
y `role` (`admin`, `member` o `viewer`).
Devuelve los usuarios creados y los que ya existían.

## Filtros útiles de `documents.list`

Además de `limit` y `offset`, acepta `collectionId`, `parentDocumentId`, `userId`,
`statusFilter`, `sort` (`updatedAt`, `createdAt`, `title`, `index`) y `direction` (`ASC`, `DESC`).

`sort: "updatedAt"` con `direction: "DESC"` da lo último que ha tocado el equipo,
que es la forma barata de ver qué se ha movido desde la última sincronización.

## Límites

El límite máximo real de paginación es 100 **probado**.
Pedir más devuelve `400 Pagination limit is too large (max 100)`.
