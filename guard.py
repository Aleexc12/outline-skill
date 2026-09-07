#!/usr/bin/env python3
"""Hook PreToolUse: impide que un comando de shell escriba en `.outline/`.

El `deny` de la configuración solo cubre Edit y Write, así que un agente que use la shell
puede romper el estado del que salen la detección de conflictos y las barreras del borrado.
Esto para el accidente, no a alguien decidido: cualquier hook que mire cadenas se puede rodear.
"""

import json
import re
import sys

STATE = ".outline"

# El hook escribe hacia una tubería, donde Python usaría la codepage ANSI y rompería las tildes.
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Solo lectura: nada de esto puede modificar un fichero sin una redirección, que se mira aparte.
READERS = {
    "awk", "cat", "cmp", "comm", "cut", "diff", "du", "file", "find", "grep", "egrep",
    "fgrep", "head", "jq", "less", "ls", "md5sum", "more", "od", "rg", "sha256sum",
    "sort", "stat", "tail", "tree", "uniq", "wc", "git", "echo", "printf",
    "compare-object", "get-childitem", "get-content", "get-item", "measure-object",
    "resolve-path", "select-string", "test-path",
}

SPLIT = re.compile(r"\|\||&&|[;|\n]")
REDIRECT = re.compile(r">>?\s*[\"']?([^\s\"'|;&]+)")
ASSIGNMENT = re.compile(r"^\w+=")
HEREDOC = re.compile(r"<<-?\s*[\"']?(\w+)[\"']?")


def without_heredocs(command):
    """Quita el cuerpo de los heredoc, que es texto y no comandos."""
    kept, delimiter = [], None
    for line in command.split("\n"):
        if delimiter is not None:
            if line.strip() == delimiter:
                delimiter = None
            continue
        kept.append(line)
        match = HEREDOC.search(line)
        if match:
            delimiter = match.group(1)
    return "\n".join(kept)


def program(segment):
    """El ejecutable de un comando, saltando las asignaciones de entorno que lo preceden."""
    for word in segment.split():
        if ASSIGNMENT.match(word):
            continue
        return word.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].lower()
    return ""


def offence(command):
    """Por qué se bloquea el comando, o nada si no toca el estado."""
    for segment in SPLIT.split(without_heredocs(command)):
        if STATE not in segment:
            continue
        for target in REDIRECT.findall(segment):
            if STATE in target:
                return f"redirige la salida a {target}"
        name = program(segment)
        if name not in READERS:
            return f"'{name}' no es un comando de solo lectura"
    return None


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    command = (payload.get("tool_input") or {}).get("command")
    if not isinstance(command, str):
        return 0
    reason = offence(command)
    if reason is None:
        return 0
    print(
        f"Bloqueado: el comando {reason}, y eso escribe en `{STATE}/`.\n"
        f"`{STATE}/` es el estado de la máquina de la herramienta `outline`: el manifiesto, la "
        "copia en la sombra del último pull y el índice. De ahí salen la detección de conflictos "
        "y las barreras del borrado, así que tocarlo a mano las rompe en silencio.\n"
        "Se reconstruye con 'outline pull'. Para leerlo, usa cat, grep o jq.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
