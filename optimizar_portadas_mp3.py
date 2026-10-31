#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Convierte y optimiza las portadas incrustadas de todos los MP3 de una carpeta.

Dependencias: pip install pillow mutagen
Las imágenes se almacenan de nuevo DENTRO del mismo MP3 (etiqueta ID3/APIC).
"""

from __future__ import annotations

import io
import shutil
from pathlib import Path

from mutagen.id3 import ID3, ID3NoHeaderError
from PIL import Image, ImageOps, UnidentifiedImageError


# ======================================================================
# CONFIGURACIÓN: MODIFICA ESTOS VALORES SI LO NECESITAS
# ======================================================================
# Por defecto, busca los MP3 en una carpeta llamada "mp3" junto al script.
# También puedes indicar una ruta absoluta, por ejemplo:
# CARPETA_MP3 = Path(r"C:\Users\TuUsuario\Music\Mis MP3")
# CARPETA_MP3 = Path("/home/usuario/musica")
CARPETA_MP3 = Path(__file__).resolve().parent / "mp3"

ANCHO_MAXIMO = 800               # píxeles
ALTO_MAXIMO = 800                # píxeles
PESO_MAXIMO_KB = 149            # 1 KB = 1024 bytes
CALIDAD_JPEG_MAXIMA = 95        # 95 = excelente calidad, sin saltar a 100

INCLUIR_SUBCARPETAS = False     # True: también revisa subcarpetas
CREAR_COPIA_SEGURIDAD = False  # True: guarda un .mp3.bak antes de editar
FONDO_TRANSPARENCIA = (255, 255, 255)  # blanco al convertir PNG transparente
# ======================================================================


def es_jpeg_baseline(datos: bytes) -> bool:
    """Verifica que el JPEG use SOF0 (baseline), no JPEG progresivo."""
    if not datos.startswith(b"\xff\xd8"):
        return False

    posicion = 2
    while posicion < len(datos):
        if datos[posicion] != 0xFF:
            return False
        while posicion < len(datos) and datos[posicion] == 0xFF:
            posicion += 1
        if posicion >= len(datos):
            return False

        marcador = datos[posicion]
        posicion += 1

        if marcador == 0xC0:  # SOF0 = baseline DCT
            return True
        # Otros marcadores SOF = codificación no baseline.
        if marcador in (0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                        0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            return False
        if marcador in (0xD8, 0x01) or 0xD0 <= marcador <= 0xD7:
            continue  # marcadores sin longitud
        if marcador in (0xD9, 0xDA):
            return False  # fin o datos comprimidos antes de hallar SOF0
        if posicion + 2 > len(datos):
            return False
        longitud = int.from_bytes(datos[posicion:posicion + 2], "big")
        if longitud < 2 or posicion + longitud > len(datos):
            return False
        posicion += longitud

    return False


def guardar_jpeg(imagen: Image.Image, calidad: int) -> bytes:
    """JPEG siempre baseline, optimizado y sin metadatos innecesarios."""
    buffer = io.BytesIO()
    imagen.save(
        buffer,
        format="JPEG",
        quality=calidad,
        optimize=True,
        progressive=False,      # imprescindible: JPEG baseline
        subsampling=0,          # 4:4:4, conserva más detalle de color
    )
    return buffer.getvalue()


def convertir_imagen(datos: bytes, max_bytes: int) -> tuple[bytes, str]:
    """Devuelve (bytes JPEG baseline, descripción). No agranda imágenes."""
    with Image.open(io.BytesIO(datos)) as original:
        original.load()
        medidas_originales = original.size
        ya_cumple = (
            original.format == "JPEG"
            and es_jpeg_baseline(datos)
            and original.width <= ANCHO_MAXIMO
            and original.height <= ALTO_MAXIMO
            and len(datos) <= max_bytes
        )
        if ya_cumple:
            return datos, "ya cumplía todos los requisitos (sin recomprimir)"

        imagen = ImageOps.exif_transpose(original)

        # Las transparencias no existen en JPEG: se colocan sobre blanco.
        if imagen.mode in ("RGBA", "LA") or "transparency" in imagen.info:
            rgba = imagen.convert("RGBA")
            fondo = Image.new("RGB", rgba.size, FONDO_TRANSPARENCIA)
            fondo.paste(rgba, mask=rgba.getchannel("A"))
            imagen = fondo
        elif imagen.mode != "RGB":
            imagen = imagen.convert("RGB")

        # thumbnail reduce solo cuando es necesario; nunca agranda.
        imagen.thumbnail((ANCHO_MAXIMO, ALTO_MAXIMO), Image.Resampling.LANCZOS)

        # Primero probar la calidad máxima; solo comprimir más si hace falta.
        mejor = guardar_jpeg(imagen, CALIDAD_JPEG_MAXIMA)
        calidad_elegida = CALIDAD_JPEG_MAXIMA

        if len(mejor) > max_bytes:
            minimo, maximo = 1, CALIDAD_JPEG_MAXIMA - 1
            mejor = None
            while minimo <= maximo:
                calidad = (minimo + maximo) // 2
                resultado = guardar_jpeg(imagen, calidad)
                if len(resultado) <= max_bytes:
                    mejor = resultado
                    calidad_elegida = calidad
                    minimo = calidad + 1  # buscar la mayor calidad que quepa
                else:
                    maximo = calidad - 1

        # Caso excepcional: incluso calidad 1 supera el peso; reducir tamaño.
        # Normalmente no es necesario para portadas de hasta 800 x 800.
        while mejor is None:
            nuevo_ancho = max(1, int(imagen.width * 0.90))
            nuevo_alto = max(1, int(imagen.height * 0.90))
            if (nuevo_ancho, nuevo_alto) == imagen.size:
                raise ValueError("No se ha podido ajustar la imagen al peso máximo")
            imagen = imagen.resize((nuevo_ancho, nuevo_alto), Image.Resampling.LANCZOS)
            resultado = guardar_jpeg(imagen, CALIDAD_JPEG_MAXIMA)
            calidad_elegida = CALIDAD_JPEG_MAXIMA
            if len(resultado) <= max_bytes:
                mejor = resultado
            else:
                minimo, maximo = 1, CALIDAD_JPEG_MAXIMA - 1
                while minimo <= maximo:
                    calidad = (minimo + maximo) // 2
                    resultado = guardar_jpeg(imagen, calidad)
                    if len(resultado) <= max_bytes:
                        mejor = resultado
                        calidad_elegida = calidad
                        minimo = calidad + 1
                    else:
                        maximo = calidad - 1

        descripcion = (
            f"{medidas_originales[0]}x{medidas_originales[1]} -> "
            f"{imagen.width}x{imagen.height}; "
            f"{len(datos) / 1024:.1f} -> {len(mejor) / 1024:.1f} KB; "
            f"calidad {calidad_elegida}"
        )
        return mejor, descripcion


def procesar_mp3(ruta: Path, max_bytes: int) -> tuple[int, int, int]:
    """Devuelve (n.º portadas cambiadas, intactas, con error)."""
    try:
        etiquetas = ID3(ruta)
    except ID3NoHeaderError:
        print("  - Sin etiquetas ID3 con portadas: omitido")
        return 0, 0, 0

    portadas = etiquetas.getall("APIC")
    if not portadas:
        print("  - Sin imágenes incrustadas: omitido")
        return 0, 0, 0

    cambiadas = intactas = errores = 0
    for numero, portada in enumerate(portadas, start=1):
        try:
            datos_nuevos, detalle = convertir_imagen(portada.data, max_bytes)
            if datos_nuevos == portada.data and portada.mime.lower() == "image/jpeg":
                intactas += 1
                print(f"  - Portada {numero}: {detalle}")
                continue

            portada.data = datos_nuevos
            portada.mime = "image/jpeg"
            cambiadas += 1
            print(f"  - Portada {numero}: {detalle}")
        except (OSError, ValueError, UnidentifiedImageError) as exc:
            errores += 1
            print(f"  - ERROR en portada {numero}: {exc}")

    if cambiadas:
        if CREAR_COPIA_SEGURIDAD:
            copia = ruta.with_name(ruta.name + ".bak")
            if not copia.exists():
                shutil.copy2(ruta, copia)
        # Mantener ID3v2.3 si era v2.3; si no, guardar en ID3v2.4.
        version = 3 if etiquetas.version[1] == 3 else 4
        etiquetas.save(ruta, v2_version=version)

    return cambiadas, intactas, errores


def main() -> None:
    if not (1 <= CALIDAD_JPEG_MAXIMA <= 100):
        raise ValueError("CALIDAD_JPEG_MAXIMA debe estar entre 1 y 100")
    if min(ANCHO_MAXIMO, ALTO_MAXIMO, PESO_MAXIMO_KB) <= 0:
        raise ValueError("Los límites de imagen deben ser mayores que cero")

    carpeta = CARPETA_MP3.expanduser().resolve()
    carpeta.mkdir(parents=True, exist_ok=True)
    max_bytes = PESO_MAXIMO_KB * 1024
    archivos = sorted(
        (carpeta.rglob("*") if INCLUIR_SUBCARPETAS else carpeta.iterdir()),
        key=lambda ruta: str(ruta).lower(),
    )
    mp3 = [ruta for ruta in archivos if ruta.is_file() and ruta.suffix.lower() == ".mp3"]

    print(f"Carpeta: {carpeta}")
    print(f"Límites: {ANCHO_MAXIMO}x{ALTO_MAXIMO} px; {PESO_MAXIMO_KB} KB por imagen")
    print(f"MP3 encontrados: {len(mp3)}\n")

    total_cambiadas = total_intactas = total_errores = 0
    archivos_con_error = 0
    for indice, ruta in enumerate(mp3, start=1):
        print(f"[{indice}/{len(mp3)}] {ruta.relative_to(carpeta)}")
        try:
            cambiadas, intactas, errores = procesar_mp3(ruta, max_bytes)
            total_cambiadas += cambiadas
            total_intactas += intactas
            total_errores += errores
        except Exception as exc:
            archivos_con_error += 1
            print(f"  - ERROR procesando el archivo: {exc}")

    print("\n=== RESULTADO ===")
    print(f"MP3 revisados: {len(mp3)}")
    print(f"Portadas convertidas/optimizadas: {total_cambiadas}")
    print(f"Portadas que ya cumplían: {total_intactas}")
    print(f"Portadas con error: {total_errores}")
    print(f"MP3 con error de procesamiento: {archivos_con_error}")
    print("Los MP3 se modifican en su ubicación original; no se crean imágenes sueltas.")


if __name__ == "__main__":
    main()
