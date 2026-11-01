#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
OPTIMIZADOR MP3 - JPEG COMPAT v3 + INFORME DETALLADO

Optimizador_MP3_Informe_Detallado_v3.py

=================================

- Elimina etiquetas externas APE y Lyrics3.
- Conserva metadatos ID3 compatibles con Traktor.
- Mantiene PRIV, TXXX, GEOB, UFID, POPM, etc.
- Convierte portadas a JPEG Baseline RGB.
- Maximo 800x800 y 149 KiB por defecto.
- Evita recomprimir portadas compatibles.
- No modifica los bytes del audio.
- Comprueba el audio mediante SHA-256.
- Crea copias de seguridad.
- Procesa todos los MP3 automaticamente.
- Muestra resumen detallado y guarda informe TXT al finalizar.

Dependencias:
    pip install --upgrade pillow mutagen
"""

import copy
import hashlib
import re
import unicodedata
import io
import os
import sys
from contextlib import redirect_stdout
from datetime import datetime
import shutil
import tempfile
from pathlib import Path

from PIL import Image, ImageOps, JpegImagePlugin
from mutagen.id3 import (
    ID3, ID3NoHeaderError, APIC,
    TIT2, TPE1, TALB, TYER, TRCK, TCON, TXXX
)
from mutagen.apev2 import APEv2, APENoHeaderError
from mutagen.mp3 import MP3


# ==========================================================
# CONFIGURACION
# ==========================================================

CARPETA_MP3 = Path(__file__).resolve().parent / "mp3"

# Limites de imagen
ANCHO_MAXIMO = 800
ALTO_MAXIMO = 800
PESO_MAXIMO_KB = 149

CALIDAD_MAXIMA = 95
CALIDAD_MINIMA = 45

FONDO_TRANSPARENCIA = (255, 255, 255)

# Compatibilidad de imagen
FORZAR_JPEG_420 = True
ELIMINAR_EXIF_ICC = True
UNA_SOLA_PORTADA = True

# Metadatos
CONSERVAR_TRAKTOR = True
ELIMINAR_APE = True
ELIMINAR_LYRICS3 = True

# Seguridad: no borrar datos DJ guardados en APE
# sin autorizacion expresa.
PERMITIR_BORRAR_APE_TRAKTOR = False

# Si existen frames ID3 desconocidos, no arriesgar
# la perdida de datos de Traktor.
ABORTAR_SI_FRAMES_DESCONOCIDOS = True

# Procesamiento
INCLUIR_SUBCARPETAS = False
CREAR_COPIA_SEGURIDAD = True
SOLO_DIAGNOSTICO = False

# Informe al finalizar: consola y archivo TXT en la carpeta del script.
GUARDAR_INFORME_TXT = True
CARPETA_INFORMES = Path(__file__).resolve().parent
DETALLE_POR_ARCHIVO = True

PADDING_ID3 = 1024

# NUEVO PARCHE: solo se forzara la recodificacion para estos artistas/temas,
# aunque el JPEG ya cumpla el peso y la resolucion.
# Cada grupo debe aparecer en el nombre del MP3. No distingue mayusculas.
PATRONES_RECODIFICACION_FORZADA = (
    ("whigfield", "think", "of", "you"),
    ("space", "forever", "young"),  # D.J. Space'C
)
FORZAR_RECODIFICACION_TODOS = False

# Limpia segmentos JPEG de Photoshop/Adobe u otros metadatos de imagen
# si se detectan (el original de Whigfield NO los contiene).
RECODIFICAR_SI_APP13_ADOBE = True

# Con True, usa tablas Huffman JPEG convencionales en recodificacion
# forzada, a costa de unos bytes extra.
HUFFMAN_ESTANDAR_EN_RECODIFICACION = True

# Recupera los frames de Traktor desde un MP3 original o .bak SOLO si
# el hash SHA-256 del AUDIO es exactamente igual. No toca el audio.
RECUPERAR_TRAKTOR_DE_ORIGINALES = True
PREFERIR_PORTADA_APE_ORIGINAL = True
CARPETA_ORIGINALES = None  # Opcional: Path(r"D:\Musica\Originales")

# Marca ID3v2.3 TXXX con hash JPEG para NO recomprimir en futuras pasadas.
MARCA_JPEG_CORREGIDO = "PORTADA_JPEG_COMPAT_V3"

BLOQUE_LECTURA = 1024 * 1024


# ==========================================================
# FRAMES DE TRAKTOR
# ==========================================================

FRAMES_DJ = {
    "PRIV",
    "TXXX",
    "GEOB",
    "UFID",
    "POPM",
    "PCNT",
    "COMM",
    "USLT",
    "SYLT",
}

# Estos frames se conservan al guardar ID3v2.3.
# No se elimina informacion privada automaticamente.


# ==========================================================
# DETECCION FISICA DE ETIQUETAS
# ==========================================================

def leer(f, posicion, cantidad):
    f.seek(posicion)
    return f.read(cantidad)


def synchsafe(datos):
    if len(datos) != 4:
        raise ValueError("Cabecera ID3 incompleta")

    if any(byte & 0x80 for byte in datos):
        raise ValueError("Tamano ID3 incorrecto")

    return (
        (datos[0] << 21)
        | (datos[1] << 14)
        | (datos[2] << 7)
        | datos[3]
    )


def localizar_audio(ruta):
    """
    Localiza el audio entre etiquetas reconocidas.

    Reconoce:
    - ID3v2.2, v2.3 y v2.4 iniciales
    - APE inicial con cabecera y footer
    - ID3v1
    - ID3v1 extendida
    - APEv1 / APEv2 finales
    - Lyrics3 v1 / v2
    - ID3v2.4 final con footer

    No elimina datos desconocidos arbitrariamente.
    """
    total = ruta.stat().st_size
    inicio = 0
    fin = total
    encontradas = []

    with ruta.open("rb") as f:

        # Etiquetas al comienzo
        while inicio + 10 <= total:
            cab = leer(f, inicio, 10)

            if cab[:3] == b"ID3":
                version = cab[3]

                if version not in (2, 3, 4):
                    raise ValueError(
                        "Version ID3 desconocida"
                    )

                tam = synchsafe(cab[6:10])

                footer = (
                    10 if version == 4
                    and cab[5] & 0x10 else 0
                )

                siguiente = inicio + 10 + tam + footer

                if siguiente > total:
                    raise ValueError(
                        "Tamano ID3 fuera del archivo"
                    )

                encontradas.append(
                    f"ID3v2.{version}"
                )
                inicio = siguiente
                continue

            if (
                inicio + 32 <= total
                and leer(f, inicio, 8) == b"APETAGEX"
            ):
                cab_ape = leer(f, inicio, 32)
                tam = int.from_bytes(
                    cab_ape[12:16], "little"
                )

                siguiente = inicio + 32 + tam

                if (
                    tam < 32
                    or siguiente > total
                    or leer(
                        f, siguiente - 32, 8
                    ) != b"APETAGEX"
                ):
                    raise ValueError(
                        "APE inicial no reconocida"
                    )

                encontradas.append("APE inicial")
                inicio = siguiente
                continue

            break

        # Etiquetas al final
        while True:
            anterior = fin

            # ID3v1
            if (
                fin >= 128
                and leer(f, fin - 128, 3) == b"TAG"
            ):
                fin -= 128
                encontradas.append("ID3v1")
                continue

            # ID3v1 extendida
            if (
                fin >= 227
                and leer(f, fin - 227, 4) == b"TAG+"
            ):
                fin -= 227
                encontradas.append("TAG+")
                continue

            # APEv1 / APEv2
            if fin >= 32:
                pie = leer(f, fin - 32, 32)

                if pie[:8] == b"APETAGEX":
                    tam = int.from_bytes(
                        pie[12:16], "little"
                    )

                    comienzo = fin - tam

                    if tam < 32 or comienzo < inicio:
                        raise ValueError(
                            "Tamano APE incorrecto"
                        )

                    if (
                        comienzo >= inicio + 32
                        and leer(
                            f, comienzo - 32, 8
                        ) == b"APETAGEX"
                    ):
                        comienzo -= 32

                    version = int.from_bytes(
                        pie[8:12], "little"
                    )

                    encontradas.append(
                        f"APEv{version // 1000}"
                    )

                    fin = comienzo
                    continue

            # Lyrics3 v2
            if (
                fin >= 15
                and leer(f, fin - 9, 9) == b"LYRICS200"
            ):
                cifra = leer(f, fin - 15, 6)

                if not cifra.isdigit():
                    raise ValueError(
                        "Lyrics3v2 invalida"
                    )

                tam = int(cifra)
                comienzo = fin - 15 - tam

                if (
                    comienzo < inicio
                    or leer(
                        f, comienzo, 11
                    ) != b"LYRICSBEGIN"
                ):
                    raise ValueError(
                        "Lyrics3v2 sin inicio valido"
                    )

                fin = comienzo
                encontradas.append("Lyrics3v2")
                continue

            # Lyrics3 v1
            if (
                fin >= 9
                and leer(f, fin - 9, 9) == b"LYRICSEND"
            ):
                desde = max(inicio, fin - 200000)

                datos = leer(
                    f, desde, fin - desde - 9
                )

                posicion = datos.rfind(
                    b"LYRICSBEGIN"
                )

                if posicion < 0:
                    raise ValueError(
                        "Lyrics3v1 sin inicio valido"
                    )

                fin = desde + posicion
                encontradas.append("Lyrics3v1")
                continue

            # ID3v2.4 final con footer
            if fin >= 20:
                pie = leer(f, fin - 10, 10)

                if (
                    pie[:3] == b"3DI"
                    and pie[3] == 4
                ):
                    tam = synchsafe(pie[6:10])
                    comienzo = fin - 20 - tam

                    if (
                        comienzo < inicio
                        or leer(
                            f, comienzo, 3
                        ) != b"ID3"
                    ):
                        raise ValueError(
                            "ID3v2.4 final invalida"
                        )

                    fin = comienzo
                    encontradas.append(
                        "ID3v2.4 final"
                    )
                    continue

            if anterior == fin:
                break

    if inicio >= fin:
        raise ValueError(
            "No se ha localizado audio valido"
        )

    return inicio, fin, encontradas


# ==========================================================
# PROTECCION DEL AUDIO
# ==========================================================

def hash_audio(ruta, inicio, fin):
    sha = hashlib.sha256()

    with ruta.open("rb") as f:
        f.seek(inicio)
        restante = fin - inicio

        while restante:
            datos = f.read(
                min(BLOQUE_LECTURA, restante)
            )

            if not datos:
                raise IOError(
                    "Lectura incompleta del audio"
                )

            sha.update(datos)
            restante -= len(datos)

    return sha.hexdigest()


def copiar_audio(origen, destino, inicio, fin):
    with origen.open("rb") as entrada:
        with destino.open("wb") as salida:
            entrada.seek(inicio)
            restante = fin - inicio

            while restante:
                datos = entrada.read(
                    min(BLOQUE_LECTURA, restante)
                )

                if not datos:
                    raise IOError(
                        "Copia incompleta del audio"
                    )

                salida.write(datos)
                restante -= len(datos)


# ==========================================================
# PARCHE DE FOTOGRAFIAS Y RECUPERACION DE TRAKTOR
# ==========================================================

def normalizar_nombre(texto):
    texto = unicodedata.normalize("NFKD", texto).casefold()
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", texto).strip()


def requiere_recodificacion_especial(ruta):
    if FORZAR_RECODIFICACION_TODOS:
        return True
    nombre = normalizar_nombre(ruta.stem)
    palabras = set(nombre.split())
    return any(set(patron).issubset(palabras)
               for patron in PATRONES_RECODIFICACION_FORZADA)


def jpeg_marcadores(datos):
    """Inspecciona los marcadores JPEG anteriores a SOS, sin analizar pixels."""
    if not datos.startswith(b"\xff\xd8"):
        return []
    resultado = []
    pos = 2
    while pos < len(datos):
        if datos[pos] != 0xff:
            return []
        while pos < len(datos) and datos[pos] == 0xff:
            pos += 1
        if pos >= len(datos):
            return []
        marcador = datos[pos]
        pos += 1
        if marcador in (0xd9, 0xda):
            break
        if marcador == 0x01 or 0xd0 <= marcador <= 0xd7:
            continue
        if pos + 2 > len(datos):
            return []
        largo = int.from_bytes(datos[pos:pos + 2], "big")
        if largo < 2 or pos + largo > len(datos):
            return []
        resultado.append(marcador)
        pos += largo
    return resultado


def jpeg_tiene_metadatos_adobe(datos):
    """APP13 Photoshop; APP14 Adobe. APP1/2 tambien se eliminan al recodificar."""
    return any(m in (0xed, 0xee) for m in jpeg_marcadores(datos))


def marca_valida(etiquetas):
    portadas = etiquetas.getall("APIC")
    if len(portadas) != 1 or not imagen_compatible(portadas[0].data):
        return False
    huella = hashlib.sha256(portadas[0].data).hexdigest()
    return any(
        f.desc == MARCA_JPEG_CORREGIDO
        and f.text and str(f.text[0]) == huella
        for f in etiquetas.getall("TXXX")
    )


def guardar_marca(etiquetas):
    portadas = etiquetas.getall("APIC")
    if not portadas:
        return
    etiquetas.delall("TXXX:" + MARCA_JPEG_CORREGIDO)
    huella = hashlib.sha256(portadas[0].data).hexdigest()
    etiquetas.add(TXXX(encoding=0, desc=MARCA_JPEG_CORREGIDO,
                       text=[huella]))


def extraer_imagen_ape(ape):
    if ape is None:
        return None
    for clave in ("Cover Art (Front)", "Cover Art (Back)"):
        valor = ape.get(clave)
        if valor is None:
            continue
        try:
            bruto = bytes(valor)
            jpeg = bruto.split(b"\x00", 1)[-1]
            with Image.open(io.BytesIO(jpeg)) as im:
                im.verify()
            return jpeg
        except Exception:
            continue
    return None


def dimension_imagen(datos):
    with Image.open(io.BytesIO(datos)) as im:
        return im.size


def frames_traktor(etiquetas):
    """Solo frames ID3 claramente identificados como propios de Traktor."""
    for f in etiquetas.values():
        if f.FrameID not in {"PRIV", "GEOB", "UFID", "TXXX"}:
            continue
        textos = (str(getattr(f, "owner", "")),
                  str(getattr(f, "desc", "")),
                  str(getattr(f, "filename", "")))
        if any("traktor" in x.casefold() for x in textos):
            yield f


def candidatos_referencia(ruta):
    # Incluye .bak del propio archivo y original sin sufijo (1), (2), etc.
    base = re.sub(r"\s*\(\d+\)$", "", ruta.stem)
    vistos = {str(ruta.resolve())}
    carpetas = [ruta.parent]
    if CARPETA_ORIGINALES is not None:
        carpetas.append(Path(CARPETA_ORIGINALES))
    for carpeta in carpetas:
        for nombre in (ruta.name, base + ".mp3"):
            for candidato in (carpeta / (nombre + ".bak"),
                              carpeta / nombre):
                real = str(candidato.resolve())
                if real not in vistos and candidato.is_file():
                    vistos.add(real)
                    yield candidato


def recuperar_de_originales(ruta, etiquetas, sha_actual):
    """Evita confundir versiones: comprueba identidad de bytes de audio."""
    imagen_ape = None
    area_max = 0
    if not RECUPERAR_TRAKTOR_DE_ORIGINALES:
        return None
    claves = {fr.HashKey for fr in frames_traktor(etiquetas)}
    for candidato in candidatos_referencia(ruta):
        try:
            i, f, _ = localizar_audio(candidato)
            if hash_audio(candidato, i, f) != sha_actual:
                continue
            try:
                tag_ref = ID3(candidato, translate=False, load_v1=False)
            except ID3NoHeaderError:
                tag_ref = ID3()
            recuperados = 0
            for fr in frames_traktor(tag_ref):
                if fr.HashKey not in claves:
                    etiquetas.add(copy.deepcopy(fr))
                    claves.add(fr.HashKey)
                    recuperados += 1
            if recuperados:
                print(f"  Recuperados {recuperados} frames Traktor desde: {candidato.name}")
            if PREFERIR_PORTADA_APE_ORIGINAL:
                ape_ref, _ = revisar_ape_traktor(candidato)
                candidato_jpeg = extraer_imagen_ape(ape_ref)
                if candidato_jpeg:
                    w, h = dimension_imagen(candidato_jpeg)
                    if w * h > area_max:
                        imagen_ape = candidato_jpeg
                        area_max = w * h
                        print(f"  Imagen fuente APE {w}x{h}: {candidato.name}")
        except Exception as exc:
            print(f"  Referencia ignorada ({candidato.name}): {exc}")
    return imagen_ape


# ==========================================================
# COMPATIBILIDAD JPEG
# ==========================================================

def jpeg_baseline(datos):
    if not datos.startswith(b"\xff\xd8"):
        return False

    posicion = 2

    while posicion < len(datos):
        if datos[posicion] != 0xFF:
            return False

        while (
            posicion < len(datos)
            and datos[posicion] == 0xFF
        ):
            posicion += 1

        if posicion >= len(datos):
            return False

        marcador = datos[posicion]
        posicion += 1

        if marcador in (0xD9, 0xDA):
            return False

        if (
            marcador == 0x01
            or 0xD0 <= marcador <= 0xD7
        ):
            continue

        if posicion + 2 > len(datos):
            return False

        longitud = int.from_bytes(
            datos[posicion:posicion + 2], "big"
        )

        if (
            longitud < 2
            or posicion + longitud > len(datos)
        ):
            return False

        if marcador == 0xC0:
            return True

        if marcador in (
            0xC1, 0xC2, 0xC3,
            0xC5, 0xC6, 0xC7,
            0xC9, 0xCA, 0xCB,
            0xCD, 0xCE, 0xCF
        ):
            return False

        posicion += longitud

    return False


def imagen_compatible(datos):
    if len(datos) > PESO_MAXIMO_KB * 1024:
        return False

    if not jpeg_baseline(datos):
        return False

    # APP13/APP14 pueden causar incompatibilidades en algunos dispositivos.
    # No asumir que existen: se inspeccionan los bytes reales.
    if RECODIFICAR_SI_APP13_ADOBE and jpeg_tiene_metadatos_adobe(datos):
        return False

    try:
        with Image.open(io.BytesIO(datos)) as img:
            img.load()

            if img.format != "JPEG":
                return False

            if img.mode != "RGB":
                return False

            if (
                img.width > ANCHO_MAXIMO
                or img.height > ALTO_MAXIMO
            ):
                return False

            if ELIMINAR_EXIF_ICC:
                if (
                    img.info.get("exif")
                    or img.info.get("icc_profile")
                ):
                    return False

            if FORZAR_JPEG_420:
                if JpegImagePlugin.get_sampling(img) != 2:
                    return False

            return True

    except Exception:
        return False


def convertir_rgb(img):
    img = ImageOps.exif_transpose(img)

    if (
        img.mode in ("RGBA", "LA")
        or "transparency" in img.info
    ):
        rgba = img.convert("RGBA")

        fondo = Image.new(
            "RGBA",
            rgba.size,
            FONDO_TRANSPARENCIA + (255,)
        )

        fondo.alpha_composite(rgba)
        return fondo.convert("RGB")

    return img.convert("RGB")


def codificar_jpeg(img, calidad, *, modo_compatibilidad=False):
    salida = io.BytesIO()
    # optimize=False usa tablas Huffman convencionales en Pillow/libjpeg.
    # Exif, ICC y Adobe/Photoshop APP13 no se copian al JPEG generado.
    img.save(
        salida,
        format="JPEG",
        quality=calidad,
        optimize=not (modo_compatibilidad and HUFFMAN_ESTANDAR_EN_RECODIFICACION),
        progressive=False,
        subsampling=2,
        exif=b"",
        icc_profile=None
    )
    return salida.getvalue()


def optimizar_portada(datos, *, forzar=False):
    if not forzar and imagen_compatible(datos):
        return datos, False

    with Image.open(io.BytesIO(datos)) as original:
        original.load()
        imagen = convertir_rgb(original)
        # Eliminar referencias EXIF/ICC/IPTC heredadas: solo copiar pixeles.
        imagen.info.clear()
        imagen = imagen.copy()

    imagen.thumbnail(
        (ANCHO_MAXIMO, ALTO_MAXIMO),
        Image.Resampling.LANCZOS
    )

    while True:
        # Probar desde la mayor calidad hasta
        # encontrar la primera que cumple.
        for calidad in range(
            CALIDAD_MAXIMA,
            CALIDAD_MINIMA - 1,
            -1
        ):
            resultado = codificar_jpeg(
                imagen, calidad,
                modo_compatibilidad=forzar
            )

            if (
                len(resultado)
                <= PESO_MAXIMO_KB * 1024
            ):
                return resultado, True

        # Reducir dimensiones solo si ninguna
        # calidad alcanza el peso maximo.
        if imagen.size == (1, 1):
            raise ValueError(
                "No se alcanza el peso maximo"
            )

        ancho = max(
            1, int(imagen.width * 0.90)
        )
        alto = max(
            1, int(imagen.height * 0.90)
        )

        if ancho == imagen.width:
            ancho = max(1, ancho - 1)

        if alto == imagen.height:
            alto = max(1, alto - 1)

        imagen = imagen.resize(
            (ancho, alto),
            Image.Resampling.LANCZOS
        )


# ==========================================================
# PROTECCION DE METADATOS TRAKTOR
# ==========================================================

def firma_dj(etiquetas):
    """
    Obtiene una representacion de los datos DJ
    para comprobar que no cambian al guardar.
    """
    resultado = []

    for frame in etiquetas.values():
        tipo = frame.FrameID

        if tipo not in FRAMES_DJ:
            continue

        if tipo == "PRIV":
            valor = (
                frame.owner,
                frame.data
            )

        elif tipo == "GEOB":
            valor = (
                frame.mime,
                frame.filename,
                frame.desc,
                frame.data
            )

        elif tipo == "UFID":
            valor = (
                frame.owner,
                frame.data
            )

        elif tipo in ("TXXX", "COMM"):
            valor = (
                getattr(frame, "desc", ""),
                getattr(frame, "lang", ""),
                tuple(
                    str(x)
                    for x in frame.text
                )
            )

        elif tipo == "POPM":
            valor = (
                frame.email,
                frame.rating,
                frame.count
            )

        elif tipo == "PCNT":
            valor = frame.count

        elif tipo in ("USLT", "SYLT"):
            valor = (
                getattr(frame, "lang", ""),
                getattr(frame, "desc", ""),
                getattr(frame, "text", None),
                getattr(frame, "type", None),
                getattr(frame, "format", None)
            )

        else:
            continue

        resultado.append(
            (tipo, repr(valor))
        )

    return sorted(resultado)


def revisar_ape_traktor(ruta):
    """
    Detecta campos APE potencialmente asociados
    a informacion DJ que no debe borrarse sin aviso.
    """
    try:
        ape = APEv2(ruta)

    except APENoHeaderError:
        return None, []

    claves = list(ape.keys())

    patrones = (
        "traktor",
        "cue",
        "beatgrid",
        "hotcue",
        "marker",
        "loop",
    )

    sospechosas = [
        clave for clave in claves
        if any(
            patron in clave.lower()
            for patron in patrones
        )
    ]

    return ape, sospechosas


def recuperar_campos_ape(etiquetas, ape):
    """
    Recupera campos musicales basicos de APE
    si no existen ya en ID3.
    """
    if ape is None:
        return

    campos = [
        ("Title", "TIT2", TIT2),
        ("Artist", "TPE1", TPE1),
        ("Album", "TALB", TALB),
        ("Year", "TYER", TYER),
        ("Track", "TRCK", TRCK),
        ("Genre", "TCON", TCON),
    ]

    for nombre, identificador, clase in campos:
        if etiquetas.getall(identificador):
            continue

        valor = ape.get(nombre)

        if valor is None:
            continue

        # Solo recuperar valores APE de texto.
        if getattr(valor, "kind", None) != 0:
            continue

        texto = str(valor).strip()

        if not texto:
            continue

        try:
            texto.encode("latin-1")
            encoding = 0
        except UnicodeEncodeError:
            encoding = 1

        etiquetas.add(
            clase(
                encoding=encoding,
                text=[texto]
            )
        )


def recuperar_portada_ape(etiquetas, ape):
    if ape is None or etiquetas.getall("APIC"):
        return

    for clave in (
        "Cover Art (Front)",
        "Cover Art (Back)"
    ):
        valor = ape.get(clave)

        if valor is None:
            continue

        try:
            bruto = bytes(valor)
            datos = bruto.split(b"\x00", 1)[-1]

            with Image.open(io.BytesIO(datos)) as img:
                img.verify()

            etiquetas.add(
                APIC(
                    encoding=0,
                    mime="image/jpeg",
                    type=3,
                    desc="",
                    data=datos
                )
            )

            print("  Portada recuperada de APE")
            return

        except Exception:
            continue


# ==========================================================
# NORMALIZACION ID3v2.3
# ==========================================================

def preparar_etiquetas(etiquetas, *, forzar=False, fuente_ape=None):
    """
    Conserva todos los frames ID3 reconocidos,
    incluidos los utilizados por Traktor.
    """
    if etiquetas.unknown_frames:
        cantidad = len(
            etiquetas.unknown_frames
        )

        print(
            f"  AVISO: {cantidad} frames ID3 "
            "desconocidos."
        )

        if ABORTAR_SI_FRAMES_DESCONOCIDOS:
            raise ValueError(
                "Frames desconocidos: no se eliminan "
                "para proteger posibles datos DJ"
            )

        etiquetas.unknown_frames = []

    firma_original = firma_dj(etiquetas)

    etiquetas.update_to_v23()

    if CONSERVAR_TRAKTOR:
        if firma_dj(etiquetas) != firma_original:
            raise ValueError(
                "La conversion ID3v2.3 perderia "
                "metadatos DJ"
            )

    portadas = etiquetas.getall("APIC")

    if portadas or fuente_ape is not None:
        elegida = next(
            (
                p for p in portadas
                if p.type == 3
            ),
            portadas[0] if portadas else None
        )
        datos_fuente = elegida.data if elegida is not None else None
        if fuente_ape is not None and (
            datos_fuente is None or (
                forzar and (
                    dimension_imagen(fuente_ape)[0] * dimension_imagen(fuente_ape)[1]
                    > dimension_imagen(datos_fuente)[0] * dimension_imagen(datos_fuente)[1]
                )
            )
        ):
            datos_fuente = fuente_ape
            print("  Usando imagen APE original de mayor resolucion")

        datos, modificada = optimizar_portada(
            datos_fuente, forzar=forzar
        )

        if UNA_SOLA_PORTADA:
            etiquetas.delall("APIC")

            etiquetas.add(
                APIC(
                    encoding=0,
                    mime="image/jpeg",
                    type=3,
                    desc="",
                    data=datos
                )
            )

        else:
            # Mantener todas las portadas, optimizando cada una.
            # No usar esta modalidad para recuperar desde APE sin APIC.
            if not portadas:
                raise ValueError("Usa UNA_SOLA_PORTADA=True para recuperar APE")
            nuevas = []

            for portada in portadas:
                nuevos_datos, _ = optimizar_portada(
                    portada.data
                )

                nuevas.append(
                    APIC(
                        encoding=(
                            0 if portada.desc.isascii()
                            else 1
                        ),
                        mime="image/jpeg",
                        type=portada.type,
                        desc=portada.desc,
                        data=nuevos_datos
                    )
                )

            etiquetas.setall("APIC", nuevas)

        if modificada:
            print(
                "  Portada RECODIFICADA para compatibilidad"
                if forzar else "  Portada optimizada"
            )
        else:
            print(
                "  Portada conservada "
                "sin recompresion"
            )

        if UNA_SOLA_PORTADA and len(portadas) > 1:
            print(
                "  Portadas secundarias eliminadas: "
                f"{len(portadas) - 1}"
            )

        if forzar:
            guardar_marca(etiquetas)

    return etiquetas


# ==========================================================
# VERIFICACION DEL RESULTADO
# ==========================================================

def verificar(
    ruta,
    hash_original,
    longitud_original,
    firma_original,
    tenia_portada
):
    inicio, fin, encontradas = localizar_audio(ruta)

    if sorted(encontradas) != sorted(
        ["ID3v2.3", "ID3v1"]
    ):
        raise ValueError(
            "Quedan etiquetas no permitidas: "
            + str(encontradas)
        )

    if fin - inicio != longitud_original:
        raise ValueError(
            "Longitud de audio modificada"
        )

    if hash_audio(ruta, inicio, fin) != hash_original:
        raise ValueError(
            "SHA-256 del audio diferente"
        )

    etiquetas = ID3(
        ruta,
        translate=False,
        load_v1=False
    )

    if etiquetas.version[:2] != (2, 3):
        raise ValueError(
            "El archivo no contiene ID3v2.3"
        )

    if etiquetas.unknown_frames:
        raise ValueError(
            "Persisten frames ID3 desconocidos"
        )

    if CONSERVAR_TRAKTOR:
        if firma_dj(etiquetas) != firma_original:
            raise ValueError(
                "Se han alterado metadatos DJ"
            )

    portadas = etiquetas.getall("APIC")

    if tenia_portada and not portadas:
        raise ValueError(
            "Se ha perdido la portada"
        )

    if UNA_SOLA_PORTADA and tenia_portada:
        if len(portadas) != 1:
            raise ValueError(
                "No hay una unica portada"
            )

        if portadas[0].type != 3:
            raise ValueError(
                "La portada no es frontal"
            )

    for portada in portadas:
        if portada.mime != "image/jpeg":
            raise ValueError(
                "MIME de portada incorrecto"
            )

        if not imagen_compatible(portada.data):
            raise ValueError(
                "Portada fuera de los limites"
            )

    MP3(ruta)


# ==========================================================
# PROCESAMIENTO
# ==========================================================

def procesar_mp3(ruta):
    temporal = None

    print(f"\nArchivo: {ruta.name}")

    try:
        # Comprobar que el archivo original es legible
        MP3(ruta)

        inicio, fin, detectadas = localizar_audio(
            ruta
        )

        print(
            "  Etiquetas originales: "
            + (", ".join(detectadas) or "Ninguna")
        )

        ape, campos_dj_ape = revisar_ape_traktor(
            ruta
        )

        if ape is not None:
            print(
                "  Campos APE: "
                + ", ".join(ape.keys())
            )

        if campos_dj_ape:
            print(
                "  AVISO: posibles datos DJ en APE: "
                + ", ".join(campos_dj_ape)
            )

        try:
            etiquetas = ID3(
                ruta,
                translate=False,
                load_v1=True
            )

        except ID3NoHeaderError:
            etiquetas = ID3()

        print(
            "  Frames ID3: "
            + ", ".join(
                sorted({
                    f.FrameID
                    for f in etiquetas.values()
                })
            )
        )

        if SOLO_DIAGNOSTICO:
            print("  Diagnostico finalizado")
            return True

        if (
            campos_dj_ape
            and not PERMITIR_BORRAR_APE_TRAKTOR
        ):
            raise ValueError(
                "APE contiene posibles datos DJ. "
                "Se conserva el original."
            )

        if not ELIMINAR_APE and ape is not None:
            raise ValueError(
                "Para obtener solo ID3v2.3 e ID3v1 "
                "es necesario eliminar APE"
            )

        if (
            not ELIMINAR_LYRICS3
            and any(
                x.startswith("Lyrics3")
                for x in detectadas
            )
        ):
            raise ValueError(
                "Lyrics3 presente y limpieza desactivada"
            )

        # Verificar el audio primero y recuperar Traktor si hay un
        # original o .bak con IDENTICO audio (sin mezclar versiones).
        hash_original = hash_audio(ruta, inicio, fin)
        forzar = requiere_recodificacion_especial(ruta) and not marca_valida(etiquetas)
        if forzar:
            print("  PARCHE JPEG selectivo activado para este tema")

        fuente_ape = recuperar_de_originales(ruta, etiquetas, hash_original)

        # Recuperar metadatos basicos si solo estaban guardados en APE.
        recuperar_campos_ape(etiquetas, ape)
        recuperar_portada_ape(etiquetas, ape)

        # En el original de Whigfield APE contiene una portada mayor.
        if PREFERIR_PORTADA_APE_ORIGINAL and ape is not None:
            candidato_ape = extraer_imagen_ape(ape)
            if candidato_ape and (
                fuente_ape is None or
                dimension_imagen(candidato_ape)[0] * dimension_imagen(candidato_ape)[1] >
                dimension_imagen(fuente_ape)[0] * dimension_imagen(fuente_ape)[1]
            ):
                fuente_ape = candidato_ape

        etiquetas = preparar_etiquetas(
            etiquetas, forzar=forzar, fuente_ape=fuente_ape
        )

        firma_original = firma_dj(etiquetas)

        tenia_portada = bool(
            etiquetas.getall("APIC")
        )

        longitud_original = fin - inicio

        # Crear archivo temporal
        descriptor, nombre = tempfile.mkstemp(
            prefix=".mp3_limpio_",
            suffix=".mp3",
            dir=str(ruta.parent)
        )

        os.close(descriptor)
        temporal = Path(nombre)

        # Copiar solamente el audio original
        copiar_audio(
            ruta, temporal, inicio, fin
        )

        # Guardar ID3v2.3 + ID3v1.
        # v1=2 crea ID3v1 si no existia.
        etiquetas.save(
            temporal,
            v2_version=3,
            v1=2,
            v23_sep="/",
            padding=lambda _: PADDING_ID3
        )

        # Verificar ANTES de sustituir
        verificar(
            temporal,
            hash_original,
            longitud_original,
            firma_original,
            tenia_portada
        )

        # Conservar permisos del archivo
        shutil.copymode(ruta, temporal)

        # Copia de seguridad
        if CREAR_COPIA_SEGURIDAD:
            respaldo = ruta.with_name(
                ruta.name + ".bak"
            )

            if not respaldo.exists():
                shutil.copy2(
                    ruta, respaldo
                )
                print(
                    "  Copia de seguridad creada"
                )

        # Sustituir original
        os.replace(temporal, ruta)
        temporal = None

        print("  RESULTADO: CORRECTO")
        print("  ID3v2.3 + ID3v1")
        print("  Audio verificado SHA-256")
        print("  Metadatos DJ conservados")

        return True

    except Exception as error:
        print(f"  ERROR: {error}")
        print("  Archivo original conservado")
        return False

    finally:
        if temporal and temporal.exists():
            try:
                temporal.unlink()
            except OSError:
                pass


# ==========================================================
# EJECUCION AUTOMATICA
# ==========================================================

def validar_configuracion():
    if ANCHO_MAXIMO < 1 or ALTO_MAXIMO < 1:
        raise ValueError(
            "Dimensiones maximas invalidas"
        )

    if PESO_MAXIMO_KB < 1:
        raise ValueError(
            "Peso maximo invalido"
        )

    if not (
        1 <= CALIDAD_MINIMA
        <= CALIDAD_MAXIMA <= 95
    ):
        raise ValueError(
            "Calidad JPEG invalida"
        )


class DuplicarSalida:
    """Muestra los mensajes en pantalla y los captura para el informe TXT."""

    def __init__(self, pantalla, memoria):
        self.pantalla = pantalla
        self.memoria = memoria

    def write(self, texto):
        self.pantalla.write(texto)
        self.memoria.write(texto)
        return len(texto)

    def flush(self):
        self.pantalla.flush()
        self.memoria.flush()


def inspeccionar_mp3(ruta):
    """
    Inspecciona un archivo sin modificarlo. Devuelve datos objetivos,
    incluidos los metadatos y la portada antes/despues de procesar.
    Un fallo de diagnostico no cancela la reparacion del MP3.
    """
    dato = {
        'tamano': None, 'etiquetas': [], 'foto': None,
        'fotos': 0, 'dj': 0, 'id3': '-', 'observacion': None,
    }
    try:
        dato['tamano'] = ruta.stat().st_size
        _, _, deteccion = localizar_audio(ruta)
        dato['etiquetas'] = deteccion
        try:
            tags = ID3(ruta, translate=False, load_v1=False)
        except ID3NoHeaderError:
            return dato

        dato['id3'] = '.'.join(str(n) for n in tags.version[:2])
        dato['dj'] = len(list(frames_traktor(tags)))
        fotos = tags.getall('APIC')
        dato['fotos'] = len(fotos)
        if not fotos:
            return dato

        portada = next((p for p in fotos if p.type == 3), fotos[0])
        datos = portada.data
        detalle = {
            'sha': hashlib.sha256(datos).hexdigest(),
            'bytes': len(datos),
            'tipo': portada.type,
            'mime': portada.mime,
            'baseline': jpeg_baseline(datos),
            'app13': 0xED in jpeg_marcadores(datos),
            'compatible': imagen_compatible(datos),
            'dimensiones': '?',
            'formato': '?',
            'modo': '?',
        }
        try:
            with Image.open(io.BytesIO(datos)) as img:
                img.load()
                detalle['dimensiones'] = f'{img.width}x{img.height}'
                detalle['formato'] = img.format
                detalle['modo'] = img.mode
        except Exception as e:
            detalle['observacion'] = f'No se pudo leer la portada: {e}'
        dato['foto'] = detalle
    except Exception as e:
        dato['observacion'] = str(e)
    return dato


def describir_portada(datos):
    if not datos or not datos.get('foto'):
        return 'sin portada ID3'
    foto = datos['foto']
    return (f"{foto['dimensiones']}, {foto['bytes']:,} bytes, "
            f"{foto['formato']}/{foto['modo']}, "
            f"Baseline={'si' if foto['baseline'] else 'no'}")


def interpretar_error(error):
    """Explica posibles siguientes pasos sin afirmar causas no verificadas."""
    t = error.casefold()
    if 'frames desconocidos' in t:
        return ('Hay frames ID3 no reconocidos. Revisalos antes de borrarlos; '
                'pueden contener datos de programas DJ.')
    if 'ape contiene posibles datos dj' in t:
        return ('Hay posibles datos DJ en APE. El script protege el original. '
                'Revisa esos campos antes de permitir eliminarlos.')
    if 'metadatos dj' in t or 'conversion id3v2.3 perderia' in t:
        return ('Se detecto riesgo de cambiar metadatos DJ. '
                'Usa la copia de seguridad y revisa el frame indicado.')
    if 'portada' in t or 'jpeg' in t or 'imagen' in t:
        return ('La imagen no pudo convertirse o verificarse. '
                'Comprueba si la portada incrustada se abre correctamente.')
    if 'sha-256' in t or 'audio' in t or 'mpeg' in t:
        return ('La comprobacion de integridad o el analisis MPEG fallo. '
                'No sustituyas el MP3 original; revisa su estructura.')
    if 'permission' in t or 'acceso denegado' in t or 'permiso' in t:
        return ('Comprueba permisos de escritura y que el MP3 no este abierto '
                'por otro programa.')
    if 'ape' in t or 'lyrics3' in t or 'id3' in t:
        return ('Hay etiquetas incompatibles o mal formadas. '
                'Conserva el original y revisa el diagnostico de etiquetas.')
    return ('Consulta el detalle anterior del error. El archivo original '
            'se ha conservado sin sustituir.')


def evaluar_resultado(ruta, correcto, log, antes, despues, backup_ya_existe):
    """Resume acciones comprobables y avisos para cada MP3."""
    error = None
    for linea in log.splitlines():
        if linea.strip().startswith(('ERROR:', 'ERROR INESPERADO:')):
            error = linea.split(':', 1)[1].strip()
            break
    avisos = []
    for linea in log.splitlines():
        t = linea.strip()
        if t.startswith('AVISO:') or t.startswith('Referencia ignorada'):
            if t not in avisos:
                avisos.append(t)

    if antes.get('observacion'):
        avisos.append('Diagnostico previo parcial: ' + antes['observacion'])

    if not correcto:
        estado = 'ERROR'
    elif SOLO_DIAGNOSTICO:
        estado = 'DIAGNOSTICADO'
    elif despues and despues.get('observacion'):
        estado = 'CORRECTO CON AVISOS'
        avisos.append('Diagnostico posterior parcial: ' + despues['observacion'])
    else:
        estado = 'CORRECTO'

    imagen_antes = antes.get('foto')
    imagen_despues = despues.get('foto') if despues else None
    portada = 'no comprobada'
    acciones = []

    if SOLO_DIAGNOSTICO and correcto:
        portada = 'sin cambios: modo diagnostico'
    elif correcto:
        if imagen_despues and not imagen_antes:
            portada = 'anadida/recuperada'
        elif imagen_despues and imagen_antes:
            portada = ('conservada sin recompresion'
                       if imagen_antes['sha'] == imagen_despues['sha']
                       else 'reconvertida/optimizada')
        else:
            portada = 'sin portada'
            avisos.append('No se encontro portada incrustada para normalizar.')

        if antes.get('dj', 0) > despues.get('dj', 0):
            avisos.append('Se encontraron menos frames Traktor en el informe posterior.')

        if backup_ya_existe and CREAR_COPIA_SEGURIDAD:
            avisos.append('Ya existia un .bak: no se sobrescribio con el estado previo actual.')

        if imagen_despues and not imagen_despues.get('compatible', False):
            avisos.append('La inspeccion posterior no confirma compatibilidad JPEG.')

        if any('APEv' in x or x == 'APE inicial' for x in antes['etiquetas']) \
                and not any('APE' in x for x in despues['etiquetas']):
            acciones = ['APE eliminado']
            avisos.append('La etiqueta APE se ha eliminado: revisa la copia .bak si contenia datos exclusivos de APE.')
        else:
            acciones = []

        if any('Lyrics3' in x for x in antes['etiquetas']) \
                and not any('Lyrics3' in x for x in despues['etiquetas']):
            acciones.append('Lyrics3 eliminado')
        acciones.append('Audio comprobado (SHA-256)')
        acciones.append('Etiquetas ID3v2.3 + ID3v1 verificadas')
    else:
        acciones = []

    if avisos and estado == 'CORRECTO':
        estado = 'CORRECTO CON AVISOS'

    return {
        'nombre': str(ruta), 'estado': estado, 'portada': portada,
        'antes': antes, 'despues': despues, 'avisos': avisos,
        'error': error or ('Fallo sin mensaje especifico' if not correcto else None),
        'acciones': acciones,
    }


def crear_resumen(resultados, carpeta, inicio, final):
    total = len(resultados)
    diagnosticos = sum(r['estado'] == 'DIAGNOSTICADO' for r in resultados)
    correctos = sum(r['estado'] in ('CORRECTO', 'CORRECTO CON AVISOS') for r in resultados)
    con_avisos = sum(r['estado'] == 'CORRECTO CON AVISOS' for r in resultados)
    errores = [r for r in resultados if r['estado'] == 'ERROR']
    recodificadas = sum(r['portada'] == 'reconvertida/optimizada' for r in resultados)
    conservadas = sum(r['portada'] == 'conservada sin recompresion' for r in resultados)
    anadidas = sum(r['portada'] == 'anadida/recuperada' for r in resultados)
    sin_portada = sum(r['portada'] == 'sin portada' for r in resultados)
    etiquetas_limpias = sum('APE eliminado' in r['acciones'] for r in resultados)
    letras_limpias = sum('Lyrics3 eliminado' in r['acciones'] for r in resultados)
    todos_avisos = [r for r in resultados if r['avisos']]
    lineas = [
        '', '=' * 73, 'INFORME FINAL - OPTIMIZADOR MP3', '=' * 73,
        f'Fecha: {final.strftime("%d/%m/%Y %H:%M:%S")}',
        f'Carpeta: {carpeta}',
        f'Duracion del proceso: {(final - inicio).total_seconds():.1f} s',
        '', '[1] RESULTADO GENERAL',
        f'    MP3 encontrados:                   {total}',
        f'    Procesados correctamente:         {correctos}',
        f'    De ellos, con avisos:             {con_avisos}',
        f'    Analizados sin modificar:         {diagnosticos}',
        f'    Errores (original conservado):    {len(errores)}',
        '', '[2] ESTADO DE PORTADAS',
        f'    Reconvertidas/optimizadas:        {recodificadas}',
        f'    Conservadas sin recomprimir:     {conservadas}',
        f'    Recuperadas/anadidas:            {anadidas}',
        f'    MP3 sin portada tras procesar:   {sin_portada}',
        '', '[3] ETIQUETAS Y SEGURIDAD',
        f'    MP3 a los que se elimino APE:    {etiquetas_limpias}',
        f'    MP3 a los que se elimino Lyrics3:{letras_limpias:>8}',
        f'    Copias .bak activadas:           {"SI" if CREAR_COPIA_SEGURIDAD else "NO"}',
        f'    Conservacion Traktor activada:  {"SI" if CONSERVAR_TRAKTOR else "NO"}',
        f'    Solo diagnostico:                {"SI" if SOLO_DIAGNOSTICO else "NO"}',
        '', '[4] ARCHIVOS CON ERRORES / AVISOS',
    ]
    if not errores and not todos_avisos:
        lineas.append('    Ninguno.')
    for r in resultados:
        if r['estado'] == 'ERROR' or r['avisos']:
            lineas.append(f'    [{r["estado"]}] {Path(r["nombre"]).name}')
            if r['error']:
                lineas.append('      Causa: ' + r['error'])
                lineas.append('      Accion: ' + interpretar_error(r['error']))
            for aviso in r['avisos']:
                lineas.append('      Aviso: ' + aviso)
    lineas += ['', '[5] DETALLE POR ARCHIVO']
    if not total:
        lineas += ['    No se encontraron MP3. Revisa CARPETA_MP3 y las subcarpetas.']
    for i, r in enumerate(resultados, 1):
        a, d = r['antes'], r['despues']
        lineas += [
            f'  {i:02}. [{r["estado"]}] {Path(r["nombre"]).name}',
            f'      Portada: {r["portada"]}',
            f'      Antes:   {describir_portada(a)}',
        ]
        if d:
            lineas.append(f'      Despues: {describir_portada(d)}')
            lineas.append(
                f'      Etiquetas: {", ".join(d["etiquetas"]) or "ninguna"}; '
                f'frames Traktor: {d["dj"]}'
            )
        else:
            lineas.append(f'      Etiquetas originales: {", ".join(a["etiquetas"]) or "ninguna"}')
        if r['acciones']:
            lineas.append('      Comprobaciones: ' + '; '.join(r['acciones']))
        for aviso in r['avisos']:
            lineas.append('      AVISO: ' + aviso)
        if r['error']:
            lineas.append('      ERROR: ' + r['error'])
            lineas.append('      SUGERENCIA: ' + interpretar_error(r['error']))
        if DETALLE_POR_ARCHIVO:
            lineas.append('')

    lineas += ['', '[6] DIAGNOSTICO Y PROXIMOS PASOS']
    if errores:
        lineas += [
            '    ATENCION: hay MP3 que no se pudieron procesar.',
            '    Se muestran arriba con la causa encontrada y sugerencia de revision.',
        ]
    elif con_avisos:
        lineas += [
            '    Todos los MP3 procesables se han completado, pero hay avisos.',
            '    Revisa los avisos en la seccion [4] antes de borrar respaldos.',
        ]
    elif diagnosticos:
        lineas.append('    Solo diagnostico: no se modificaron los archivos.')
    elif total:
        lineas.append('    No se detectaron errores ni avisos durante el procesamiento.')
    else:
        lineas.append('    No hay archivos que evaluar.')
    lineas += [
        '    NOTA: la verificacion interna no garantiza que un reproductor',
        '    especifico muestre la portada; prueba una copia en el dispositivo.',
        '=' * 73,
    ]
    return '\n'.join(lineas)


def main():
    validar_configuracion()
    CARPETA_MP3.mkdir(parents=True, exist_ok=True)

    if INCLUIR_SUBCARPETAS:
        candidatos = CARPETA_MP3.rglob('*')
    else:
        candidatos = CARPETA_MP3.iterdir()

    archivos = sorted(
        (p for p in candidatos if p.is_file() and p.suffix.lower() == '.mp3'),
        key=lambda p: str(p).lower()
    )
    inicio = datetime.now()
    encabezado = '\n'.join([
        '=' * 73,
        'OPTIMIZADOR MP3 - JPEG COMPAT v4 + INFORME DETALLADO',
        '=' * 73,
        f'Carpeta: {CARPETA_MP3}',
        f'Archivos encontrados: {len(archivos)}',
        f'Imagen maxima: {ANCHO_MAXIMO}x{ALTO_MAXIMO}; '
        f'peso maximo: {PESO_MAXIMO_KB} KiB',
        'Formato: JPEG Baseline RGB, 4:2:0; etiquetas: ID3v2.3 + ID3v1',
        'Recodificacion especial: Whigfield / D.J. Space C',
        f'Modo diagnostico: {"SI" if SOLO_DIAGNOSTICO else "NO"}',
        '=' * 73,
    ])
    print(encabezado)
    resultados = []
    registros = []

    for numero, archivo in enumerate(archivos, 1):
        antes = inspeccionar_mp3(archivo)
        respaldo_ya_existe = archivo.with_name(archivo.name + '.bak').exists()
        memoria = io.StringIO()
        print(f'\n[{numero}/{len(archivos)}] {archivo.name}')
        with redirect_stdout(DuplicarSalida(sys.stdout, memoria)):
            try:
                correcto = procesar_mp3(archivo)
            except Exception as e:
                correcto = False
                print(f'  ERROR INESPERADO: {type(e).__name__}: {e}')
                print('  Archivo original conservado si no se habia sustituido.')
        log = memoria.getvalue()
        registros.append(f'[{numero}/{len(archivos)}] {archivo.name}\n{log}')
        despues = inspeccionar_mp3(archivo) if correcto and not SOLO_DIAGNOSTICO else None
        evaluacion = evaluar_resultado(
            archivo, correcto, log, antes, despues, respaldo_ya_existe
        )
        resultados.append(evaluacion)

    final = datetime.now()
    resumen = crear_resumen(resultados, CARPETA_MP3, inicio, final)
    print(resumen)

    if GUARDAR_INFORME_TXT:
        try:
            CARPETA_INFORMES.mkdir(parents=True, exist_ok=True)
            nombre = f'informe_mp3_{final.strftime("%Y%m%d_%H%M%S_%f")}.txt'
            destino = CARPETA_INFORMES / nombre
            contenido = encabezado + '\n\n--- REGISTRO DE PROCESAMIENTO ---\n\n'
            contenido += '\n'.join(registros) + '\n' + resumen + '\n'
            destino.write_text(contenido, encoding='utf-8')
            print(f'Informe TXT guardado en: {destino}')
        except Exception as e:
            print('AVISO: no se pudo guardar el informe TXT:', e)
            print('El resumen anterior sigue disponible en la consola.')


if __name__ == '__main__':
    main()
