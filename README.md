# Scrip-portadas-mp3

🎵 Optimizador de portadas MP3

Script desarrollado en Python para convertir y optimizar automáticamente las imágenes incrustadas en archivos MP3.

El programa permite convertir las portadas a JPEG Baseline, limitar sus dimensiones y reducir su peso manteniendo la mayor calidad posible.

Las imágenes se modifican directamente dentro de los archivos MP3, sin necesidad de extraerlas manualmente.

✨ Características

- ✅ Conversión a JPEG Baseline: convierte las portadas a JPEG no progresivo.
- ✅ Resolución máxima configurable: por defecto, 800 × 800 píxeles.
- ✅ Sin ampliaciones: mantiene las dimensiones originales cuando son inferiores al límite.
- ✅ Peso máximo configurable: por defecto, 149 KB por imagen.
- ✅ Compresión inteligente: busca la máxima calidad JPEG que cumpla el límite de peso.
- ✅ Mínima pérdida de calidad: evita recomprimir imágenes que ya cumplen los requisitos.
- ✅ Procesamiento automático: analiza todos los archivos MP3 de la carpeta configurada.
- ✅ Compatibilidad con subcarpetas: permite procesar también los MP3 de directorios internos.
- ✅ Múltiples portadas: procesa todas las imágenes incrustadas en cada MP3.
- ✅ Conservación de metadatos: mantiene el título, artista, álbum y demás etiquetas.
- ✅ Copias de seguridad opcionales: permite guardar los archivos originales antes de modificarlos.
- ✅ Resumen de resultados: muestra los archivos procesados, optimizados y los posibles errores.

📋 Requisitos

Para utilizar el script necesitas:

- Python 3.9 o superior.
- Pillow.
- Mutagen.

Instalación de dependencias

Abre una terminal y ejecuta:
```
pip install pillow mutagen
```
También puedes utilizar:
```
python -m pip install pillow mutagen
```
📁 Estructura de carpetas

La estructura recomendada es la siguiente:
```
Optimizador-MP3/
│
├── optimizar_portadas_mp3.py
├── README.md
│
└── mp3/
    ├── cancion1.mp3
    ├── cancion2.mp3
    ├── cancion3.mp3
    └── cancion4.mp3
```
Por defecto, el programa busca los archivos MP3 dentro de la carpeta "mp3", situada junto al script.

Si esta carpeta no existe, se crea automáticamente.

También puedes indicar cualquier otra carpeta modificando la configuración.

⚙️ Configuración

Todas las opciones principales se encuentran al principio del archivo "optimizar_portadas_mp3.py".

```
CARPETA_MP3 = Path(__file__).resolve().parent / "mp3"

ANCHO_MAXIMO = 800
ALTO_MAXIMO = 800
PESO_MAXIMO_KB = 149
CALIDAD_JPEG_MAXIMA = 95

INCLUIR_SUBCARPETAS = False
CREAR_COPIA_SEGURIDAD = False

FONDO_TRANSPARENCIA = (255, 255, 255)
```
📂 Carpeta de los archivos MP3

Puedes utilizar cualquier carpeta de tu ordenador.

Windows:
```
CARPETA_MP3 = Path(r"C:\Users\Usuario\Music")
```
Linux:
```
CARPETA_MP3 = Path("/home/usuario/musica")

Carpeta junto al script (predeterminado):

CARPETA_MP3 = Path(__file__).resolve().parent / "mp3"
```
🖼️ Resolución máxima

Establece las dimensiones máximas permitidas para las imágenes.
```
ANCHO_MAXIMO = 800
ALTO_MAXIMO = 800
```
Por defecto, ninguna portada procesada superará los 800 × 800 píxeles.

El programa mantiene las proporciones originales.

Ejemplos:
```
Resolución original| Resolución final
2000 × 2000| 800 × 800
1200 × 900| 800 × 600
500 × 500| 500 × 500
600 × 400| 600 × 400
300 × 300| 300 × 300
```
Las imágenes pequeñas no se amplían.

Si una imagen supera el límite de peso incluso después de comprimirla, el programa puede reducir adicionalmente su resolución.

💾 Peso máximo

Permite establecer el tamaño máximo de las imágenes incrustadas.
```
PESO_MAXIMO_KB = 149
```
El valor predeterminado es 149 KB por imagen.

Puedes modificarlo fácilmente:
```
PESO_MAXIMO_KB = 200
```
El programa ajustará automáticamente la calidad JPEG para intentar mantener la mayor calidad posible sin superar el límite.

🎨 Calidad JPEG

Permite establecer la calidad máxima utilizada durante la conversión.
```
CALIDAD_JPEG_MAXIMA = 95
```
El programa intentará utilizar la mayor calidad posible dentro del límite establecido.

Si la imagen supera el peso máximo, reducirá progresivamente la calidad hasta encontrar un resultado válido.

Si no consigue cumplir el límite reduciendo solamente la calidad, disminuirá las dimensiones de la imagen.

📂 Procesar subcarpetas

Por defecto, el programa analiza únicamente los archivos MP3 de la carpeta principal.
```
INCLUIR_SUBCARPETAS = False
```
Para procesar también los archivos de las subcarpetas:
```
INCLUIR_SUBCARPETAS = True
```
🔒 Copias de seguridad

El programa puede crear una copia de seguridad de cada MP3 antes de modificarlo.

Por defecto, esta función está desactivada:
```
CREAR_COPIA_SEGURIDAD = False
```
Para activarla:
```
CREAR_COPIA_SEGURIDAD = True
```
Las copias se guardan con la extensión ".mp3.bak".

Ejemplo:
```
cancion.mp3
cancion.mp3.bak
```
Si la copia de seguridad ya existe, no se sobrescribe.

🖼️ Fondo de imágenes transparentes

El formato JPEG no admite transparencias.

Por este motivo, las imágenes PNG transparentes se convierten utilizando un fondo blanco.
```
FONDO_TRANSPARENCIA = (255, 255, 255)
```
Puedes cambiar el color modificando sus valores RGB.

🚀 Ejecución

Una vez instaladas las dependencias y configurada la carpeta, ejecuta el programa.

Windows:
```
python optimizar_portadas_mp3.py
```
Linux:
```
python3 optimizar_portadas_mp3.py
```
El programa comenzará automáticamente a procesar todos los archivos MP3 encontrados.

No es necesario introducir comandos adicionales ni confirmar cada archivo.

🔄 Funcionamiento

El proceso de optimización se realiza automáticamente siguiendo estos pasos:

1. Análisis del MP3

El programa abre el archivo y busca las imágenes incrustadas en sus etiquetas ID3.

2. Comprobación de la imagen

Analiza:

- Formato de imagen.
- Tipo de JPEG (Baseline o progresivo).
- Resolución.
- Peso.
- Compatibilidad.

3. Conversión de formato

Cuando es necesario, convierte la portada al formato JPEG Baseline.

4. Ajuste de resolución

Si la imagen supera las dimensiones máximas, reduce su tamaño manteniendo las proporciones originales.

Las imágenes que ya tienen una resolución inferior no se amplían.

5. Optimización de calidad

El programa busca la calidad JPEG más alta posible que permita cumplir el límite de peso establecido.

6. Reducción adicional

Si la imagen sigue superando el límite incluso con una compresión elevada, reduce progresivamente su resolución.

7. Actualización del MP3

La portada optimizada sustituye a la imagen original dentro del archivo MP3.

8. Siguiente archivo

El proceso continúa automáticamente hasta terminar con todos los archivos encontrados.

📊 Resultados

Al finalizar, el programa muestra un resumen del procesamiento.

Ejemplo:

=== RESULTADO ===
MP3 revisados: 50
Portadas convertidas/optimizadas: 38
Portadas que ya cumplían: 12
Portadas con error: 0
MP3 con error de procesamiento: 0

Esto permite comprobar rápidamente cuántos archivos se han procesado correctamente.

❓ Preguntas frecuentes

¿Se modifica la calidad del audio?

No.

El programa únicamente modifica las imágenes incrustadas dentro de los archivos MP3.

El audio no se recodifica ni se comprime.

¿Se conservan los metadatos?

Sí.

El programa conserva las etiquetas ID3, incluyendo información como:

- Título.
- Artista.
- Álbum.
- Año.
- Género.
- Número de pista.

Únicamente se actualizan las imágenes que requieren optimización.

¿Qué ocurre si una imagen ya cumple los requisitos?

No se modifica.

Esto evita realizar una nueva compresión y perder calidad innecesariamente.

¿Qué ocurre si una imagen mide menos de 800 × 800 píxeles?

Se mantiene su resolución original, siempre que pueda cumplir el límite de peso establecido.

Por ejemplo, una imagen de 500 × 500 píxeles no se ampliará a 800 × 800.

¿Qué ocurre si la portada supera los 149 KB?

El programa reducirá su calidad JPEG hasta encontrar la mejor compresión posible que no supere el límite.

Si no es suficiente, reducirá también sus dimensiones.

¿Qué ocurre con las imágenes PNG?

Se convierten automáticamente al formato JPEG Baseline.

Si contienen transparencia, se utiliza el color de fondo configurado.

¿Se generan imágenes independientes?

No.

Las imágenes permanecen incrustadas dentro de los archivos MP3.

¿Qué ocurre si un MP3 no tiene portada?

El programa lo omite y continúa con el siguiente archivo.

¿Puedo ejecutar el script varias veces?

Sí.

Las imágenes que ya cumplen los requisitos no vuelven a comprimirse.

Esto evita una pérdida innecesaria de calidad.

¿Se modifican los MP3 originales?

Sí.

El programa actualiza las portadas directamente dentro de los archivos originales.

Se recomienda activar las copias de seguridad antes de procesar una colección importante.

⚠️ Consideraciones importantes

- El script está diseñado para archivos MP3 que utilizan etiquetas ID3.
- El límite de peso se aplica individualmente a cada imagen incrustada.
- La conversión a JPEG puede producir una pequeña pérdida de calidad.
- El programa intenta minimizar esta pérdida utilizando la mejor calidad posible.
- No se modifica el contenido de audio.
- Se recomienda hacer una copia de seguridad antes de utilizar el programa por primera vez.

🛠️ Tecnologías utilizadas

- Python: lenguaje de programación principal.
- Pillow: procesamiento, conversión y compresión de imágenes.
- Mutagen: lectura y modificación de etiquetas ID3 en archivos MP3.
