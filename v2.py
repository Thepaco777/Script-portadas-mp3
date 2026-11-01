
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
OPTIMIZADOR MP3 - ID3v2.3 + ID3v1
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

Dependencias:
    pip install --upgrade pillow mutagen
"""

import hashlib
import io
import os
import shutil
import tempfile
from pathlib import Path

from PIL import Image, ImageOps, JpegImagePlugin
from mutagen.id3 import (
    ID3, ID3NoHeaderError, APIC,
    TIT2, TPE1, TALB, TYER, TRCK, TCON
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
PERMITIR_BORRAR_APE_TRAKTOR = True

# Si existen frames ID3 desconocidos, no arriesgar
# la perdida de datos de Traktor.
ABORTAR_SI_FRAMES_DESCONOCIDOS = True

# Procesamiento
INCLUIR_SUBCARPETAS = False
CREAR_COPIA_SEGURIDAD = False
SOLO_DIAGNOSTICO = False

PADDING_ID3 = 1024

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


def codificar_jpeg(img, calidad):
    salida = io.BytesIO()

    img.save(
        salida,
        format="JPEG",
        quality=calidad,
        optimize=True,
        progressive=False,
        subsampling=2
    )

    return salida.getvalue()


def optimizar_portada(datos):
    if imagen_compatible(datos):
        return datos, False

    with Image.open(io.BytesIO(datos)) as original:
        original.load()
        imagen = convertir_rgb(original)

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
                imagen, calidad
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

def preparar_etiquetas(etiquetas):
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

    if portadas:
        elegida = next(
            (
                p for p in portadas
                if p.type == 3
            ),
            portadas[0]
        )

        datos, modificada = optimizar_portada(
            elegida.data
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
            # Mantener todas las portadas,
            # optimizando cada una.
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
            print("  Portada optimizada")
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

        # Recuperar metadatos basicos si solo
        # estaban guardados en APE.
        recuperar_campos_ape(etiquetas, ape)
        recuperar_portada_ape(etiquetas, ape)

        etiquetas = preparar_etiquetas(
            etiquetas
        )

        firma_original = firma_dj(etiquetas)

        tenia_portada = bool(
            etiquetas.getall("APIC")
        )

        hash_original = hash_audio(
            ruta, inicio, fin
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


def main():
    validar_configuracion()

    CARPETA_MP3.mkdir(
        parents=True,
        exist_ok=True
    )

    if INCLUIR_SUBCARPETAS:
        candidatos = CARPETA_MP3.rglob("*")
    else:
        candidatos = CARPETA_MP3.iterdir()

    archivos = sorted(
        [
            p for p in candidatos
            if p.is_file()
            and p.suffix.lower() == ".mp3"
        ],
        key=lambda p: str(p).lower()
    )

    print("=" * 60)
    print("OPTIMIZADOR MP3 - COMPATIBILIDAD ID3v2.3")
    print("=" * 60)

    print(f"Carpeta: {CARPETA_MP3}")
    print(f"Archivos: {len(archivos)}")
    print(
        f"Imagen maxima: "
        f"{ANCHO_MAXIMO}x{ALTO_MAXIMO}"
    )
    print(
        f"Peso maximo: {PESO_MAXIMO_KB} KiB"
    )
    print("JPEG: Baseline RGB")
    print("Etiquetas finales: ID3v2.3 + ID3v1")
    print(
        f"Conservar Traktor: {CONSERVAR_TRAKTOR}"
    )
    print(
        f"Copias de seguridad: "
        f"{CREAR_COPIA_SEGURIDAD}"
    )
    print(
        f"Solo diagnostico: {SOLO_DIAGNOSTICO}"
    )

    print("=" * 60)

    correctos = 0
    errores = 0

    for numero, archivo in enumerate(
        archivos, 1
    ):
        print(
            f"\n[{numero}/{len(archivos)}]"
        )

        if procesar_mp3(archivo):
            correctos += 1
        else:
            errores += 1

    print("\n" + "=" * 60)
    print("RESUMEN FINAL")
    print("=" * 60)

    print(
        f"MP3 encontrados: {len(archivos)}"
    )
    print(f"Correctos: {correctos}")
    print(f"Errores: {errores}")

    print("=" * 60)
    print("PROCESO FINALIZADO")
    print("=" * 60)


if __name__ == "__main__":
    main()
