# PanTilt: explicación del código Python y OpenCV

Esta guía explica la aplicación implementada con el detalle necesario para
leer el código y presentarlo en clase. Conviene consultar los archivos
vinculados mientras se lee. Los ejemplos breves ilustran los algoritmos
reales, pero omiten algunas validaciones y tareas de limpieza; no sustituyen
los archivos del programa.

Los nombres de funciones, variables y botones se conservan en inglés para
que puedas encontrarlos directamente en el código y en la interfaz.

## 1. Objetivo y alcance del proyecto

El objetivo es mantener un objeto de seguimiento cerca del centro de la
imagen mediante un soporte de cámara con dos ejes: **pan**, giro horizontal,
y **tilt**, inclinación vertical. Python lee los fotogramas de la cámara,
detecta un objetivo, calcula una corrección y la envía al Arduino. El Arduino
aplica esa corrección a los ángulos de dos servomotores. Flask permite manejar
los controles y ver el video en un navegador, en lugar de usar `cv2.imshow()`.

La aplicación tiene tres modos:

| Modo | Objetivo | Condición para moverse |
| --- | --- | --- |
| Static | Ninguno | No hay seguimiento automático. El botón Home sigue disponible. |
| Face | Centro del rectángulo del rostro más grande | Rostro válido y seguimiento de servos habilitado. |
| Hand | Punta del dedo índice | Puntos de la mano válidos, gesto de pinza activo y seguimiento habilitado. |

Es un sistema de realimentación visual: una corrección cambia la dirección
de la cámara y la siguiente imagen proporciona un nuevo error. El programa
no mide la posición del eje de los servos. El modelo de rostros no identifica
personas y en este proyecto no se entrena ninguno de los modelos.

El desarrollo propio comprende el procesamiento de imágenes, la selección
del objetivo, la activación por gestos, la lógica de movimiento, la interfaz,
la gestión del estado y la integración entre componentes.

## 2. Responsabilidades y orden de lectura

| Archivo | Objetos o funciones principales | Responsabilidad |
| --- | --- | --- |
| [web_app.py](../python/web_app.py) | `main`, `create_app` | Interpretar las opciones de terminal y convertir peticiones HTTP en llamadas al servicio. |
| [tracking_service.py](../python/tracking_service.py) | `TrackingService`, `tracking_frame` | Administrar el hardware y coordinar cada fotograma de la sesión. |
| [tracking_targets.py](../python/tracking_targets.py) | `TrackingTarget`, `face_target`, `hand_target` | Convertir resultados de los modelos en un objetivo común y dibujar los marcadores. |
| [hand_detector.py](../python/hand_detector.py) | `HandDetector` | Adaptar las imágenes de OpenCV a la entrada de MediaPipe. |
| [motion_control.py](../python/motion_control.py) | `MotionController`, `calculate_axis_step` | Convertir el error del objetivo en comandos relativos limitados. |
| [tracking_config.py](../python/tracking_config.py) | Constantes | Centralizar resoluciones, rutas, signos y parámetros de ajuste. |
| [camera_devices.py](../python/camera_devices.py) | `list_cameras` | Encontrar dispositivos físicos de captura V4L2 utilizables. |
| [local_settings.py](../python/local_settings.py) | Funciones de carga, guardado y validación | Conservar el ajuste de pinza entre ejecuciones. |
| [app.js](../python/static/app.js) | `render`, `command`, `poll` | Sincronizar los controles del navegador con el estado del servidor. |
| [Programa de Arduino](../firmware/pan_tilt/pan_tilt.ino) | `setup`, `loop`, `approach` | Recibir comandos y actualizar las posiciones de los servos. |

Empieza por `TrackingService._run()`: muestra cómo se conectan los módulos.
Después revisa la extracción del objetivo y el control de movimiento, antes
de estudiar la interfaz web.

## 3. Qué hace OpenCV en este proyecto

Un fotograma es un arreglo de NumPy. A 720p tiene forma `(720, 1280, 3)`:
alto, ancho y tres canales de color BGR. Las coordenadas usan `x` para la
posición horizontal y `y` para la vertical, pero el acceso al arreglo se
escribe `frame[y, x]`.

| Operación | Uso en el programa |
| --- | --- |
| `cv2.VideoCapture(source, cv2.CAP_V4L2)` | Abrir una cámara en Linux. |
| `camera.set(...)` | Solicitar MJPEG, 1280×720, 30 FPS y un búfer de captura pequeño. |
| `camera.read()` | Obtener un indicador de éxito y la siguiente imagen decodificada. |
| `cv2.resize(..., INTER_AREA)` | Reducir la imagen para inferencia conservando sus proporciones. |
| `cv2.cvtColor(..., COLOR_BGR2RGB)` | Convertir BGR a RGB antes de usar MediaPipe. |
| `cv2.rectangle`, `line`, `circle`, `drawMarker`, `putText` | Dibujar información de detección y control en la copia de vista previa. |
| `cv2.imencode('.jpg', ...)` | Codificar el fotograma anotado para enviarlo al navegador. |
| `camera.release()` | Liberar la cámara al terminar o cambiar de dispositivo. |

OpenCV es central en el manejo del video, pero no es el modelo que detecta
rostros o manos. La inferencia de YOLO se ejecuta mediante Ultralytics y los
puntos de la mano provienen de MediaPipe. La captura sigue el ciclo
`VideoCapture` / `read` / `release` documentado por OpenCV.
[Tutorial de video de OpenCV](https://docs.opencv.org/4.x/dd/d43/tutorial_py_video_display.html).

## 4. Inicio del programa y recorrido de un fotograma

`web_app.main()` construye un `TrackingService` e inicia Flask en
`127.0.0.1`. Abrir la página no abre todavía la cámara ni el puerto del
Arduino. Al pulsar **Start session**, el navegador llama a
`POST /api/session/start`, que inicia un hilo de trabajo mediante
`TrackingService.start()`.

El hilo comprueba que existan ambos archivos de modelos, inicializa YOLO y
MediaPipe, abre la cámara y crea un `MotionController` sin conexión serial.
Los dos modelos se cargan incluso en Static, pero en ese modo ninguno ejecuta
inferencia: no se analizan los fotogramas para detectar objetivos.

El ciclo de procesamiento sigue estos pasos:

1. Copiar la configuración solicitada y su `revision` mientras se mantiene
   el bloqueo de acceso a los datos compartidos.
2. Aplicar cambios de cámara y cerrar la salida virtual anterior si cambia
   la fuente de video.
3. Abrir el puerto serial solamente cuando el seguimiento o Home lo necesita.
   Esperar dos segundos al arranque del Arduino y enviar una corrección cero.
4. Avanzar una secuencia Home pendiente y reiniciar el historial del filtro
   y de la pinza si cambiaron los controles.
5. Leer la cámara en `raw`; si falla la lectura, terminar la sesión con error.
6. Crear `preview = raw.copy()` y, si hay seguimiento, una imagen reducida.
7. Ejecutar únicamente el detector seleccionado y obtener un `TrackingTarget`
   junto con los marcadores de visualización.
8. Volver a comprobar `revision` y la solicitud de parada antes de mover los
   servos. Descartar resultados antiguos si cambiaron los controles durante
   la inferencia.
9. Enviar la imagen limpia `raw` a la cámara virtual, si está habilitada.
10. Añadir a `preview` la marca central y la posición filtrada; codificarla
    para el navegador si corresponde y publicar el estado y los FPS.

```mermaid
flowchart TD
    Read[OpenCV camera.read] --> Raw[raw: arreglo BGR en resolución completa]
    Raw --> Small[tracking_frame: imagen reducida]
    Small --> Mode{Modo seleccionado}
    Mode -->|Face| YOLO[YOLO → centro del rostro más grande]
    Mode -->|Hand| MP[MediaPipe → puntos → pinza y punta del índice]
    YOLO --> Target[TrackingTarget]
    MP --> Target
    Target --> Check[Comprobar revisión y calcular movimiento]
    Check --> Serial[Corrección relativa por serial]
    Raw --> Clean[pyvirtualcam: video limpio]
    Raw --> Copy[preview = raw.copy]
    Target --> Copy
    Copy --> JPEG[Codificación JPEG opcional]
    JPEG --> Browser[Flujo MJPEG de Flask → imagen del navegador]
```

Las ramas representan usos de los datos, no hilos independientes de
procesamiento. La captura, la inferencia y la salida se ejecutan de forma
secuencial en el mismo hilo de trabajo.

## 5. Salida HD e imágenes pequeñas para inferencia

`tracking_config.py` solicita una captura de **1280×720**. La cámara puede
negociar otra resolución, por lo que el servicio informa las dimensiones
reales de los fotogramas, sin asumir que la solicitud se cumplió.

`tracking_frame()` calcula:

```python
scale = min(1.0, 640 / max(height, width))
new_width = round(width * scale)
new_height = round(height * scale)
```

Para 1280×720, `scale = 0.5` y el resultado es 640×360. Una imagen de 640×480
no cambia. El código no deforma una imagen 16:9 para convertirla en 4:3 ni
amplía una entrada pequeña. `INTER_AREA` es el método de interpolación usado
al reducirla.

| Etapa | Tamaño habitual con captura a 720p |
| --- | --- |
| `raw` y `preview` | 1280×720 |
| Copia para el detector seleccionado | 640×360 |
| Ajuste de tamaño de inferencia de YOLO | `imgsz=320`; la biblioteca gestiona el preprocesamiento y relleno |
| Entrada a MediaPipe | 640×360 RGB; la biblioteca gestiona el procesamiento interno del modelo |
| Flujo hacia el navegador | JPEG de 1280×720, calidad 90 |
| Entrada a la cámara virtual | BGR de 1280×720, sin marcadores de vista previa |

El tamaño visual definido por CSS en el navegador no es la resolución de
captura. La salida virtual no decodifica el JPEG del navegador ni reutiliza
la imagen reducida de inferencia: recibe `raw` directamente. Aun así, puede
haber conversión del formato de píxeles en el sistema de cámara virtual y
compresión en la aplicación de videollamadas.

## 6. Detección de rostros y conversión de coordenadas

El hilo de trabajo llama a:

```python
result = face_model.predict(
    detection_frame, imgsz=320, conf=0.5, verbose=False
)[0]
```

Se hace inferencia con los pesos locales especializados en rostros, no una
detección genérica de personas. La API devuelve rectángulos delimitadores;
consulta la [referencia de predicción de Ultralytics](https://docs.ultralytics.com/modes/predict/).

`face_target()` obtiene `result.boxes.xyxy`, lo convierte en un arreglo de
NumPy y usa `largest_box_index()` para seleccionar el rectángulo de mayor área:

```text
area = (x2 - x1) × (y2 - y1)
```

Las coordenadas del resultado se refieren a la imagen enviada al detector.
El código las convierte a coordenadas de vista previa usando
`preview_width / source_width` y `preview_height / source_height`.
Un rectángulo `(100, 50, 200, 150)` en una entrada de 640×360 se convierte en
`(200, 100, 400, 300)` en una vista de 1280×720. Su centro es `(300, 200)`.

El objetivo es ese centro, no un punto facial específico. Si no hay
rectángulos, el objetivo queda inactivo y el controlador mantiene la posición.
Elegir el rectángulo más grande es sencillo y determinista, pero puede
cambiar de persona si cambian sus tamaños aparentes; no se conserva una
identidad entre fotogramas.

## 7. Puntos de la mano y gesto de pinza relativo

`HandDetector.detect()` convierte la imagen BGR reducida a RGB, crea una
imagen de MediaPipe y llama a `detect_for_video()`. La marca de tiempo es:

```python
timestamp_ms = max(last_timestamp_ms + 1, time.monotonic_ns() // 1_000_000)
```

Esto garantiza marcas de tiempo estrictamente crecientes, incluso si llegan
fotogramas muy próximos o el usuario cambia de modo. El detector está
configurado para una mano y usa 0.6 como umbral de confianza de detección,
presencia y seguimiento. La aplicación usa los 21 puntos normalizados de
MediaPipe; no usa una confianza individual para cada punta de dedo.
[MediaPipe Hand Landmarker](https://ai.google.dev/edge/mediapipe/solutions/vision/hand_landmarker/python).

`hand_target()` transforma cada punto normalizado a píxeles de la imagen HD:

```python
pixel_x = landmark.x * width
pixel_y = landmark.y * height
```

Los índices relevantes son 4, punta del pulgar; 8, punta del índice; 5,
nudillo del índice; y 17, nudillo del meñique. El objetivo de movimiento por
defecto es el punto 8. La función también admite el centro de la palma, pero
esa alternativa no está disponible en la interfaz.

La pinza se calcula así:

```text
r = distancia(punta del pulgar, punta del índice)
    / distancia(nudillo del índice, nudillo del meñique)
```

Una separación de 15 píxeles entre puntas con una palma de 100 píxeles da
`r = 0.15`. Al acercar la mano podrían obtenerse 30 píxeles y 200 píxeles,
respectivamente: la proporción sigue siendo 0.15. Por eso el ajuste es un
porcentaje del ancho de la palma y no una cantidad fija de píxeles.
Las distancias se calculan después de convertir las coordenadas a píxeles,
para tener en cuenta la proporción de la imagen.

El umbral predeterminado es 0.20 y la interfaz acepta valores entre 0.05 y
0.60. Se aplica **histéresis**, es decir, umbrales distintos para activar y
soltar el gesto:

- Una pinza inactiva se activa cuando `r <= threshold`.
- Una pinza activa se mantiene mientras `r <= threshold + 0.06`.
- Con el ajuste predeterminado, se activa al 20% y se suelta por encima del 26%.

Esta separación evita cambios rápidos entre activo e inactivo cerca del
límite. Se desactiva el movimiento si faltan puntos necesarios, no son
finitos, están fuera de la imagen o el ancho de la palma es cero.
Los cambios uniformes de tamaño se compensan, pero la rotación, la
perspectiva y los dedos ocultos todavía pueden afectar la proporción.

## 8. Del objetivo a los comandos de los servos

`TrackingTarget` contiene `position`, `active` y `status`. Esta estructura
común permite que `MotionController` funcione sin saber si el objetivo
proviene de un rostro o de una mano.

### 8.1 Condición de movimiento y suavizado

Para moverse deben ser verdaderos tanto `enabled` como `target.active`.
En caso contrario se limpia el filtro y se envían correcciones cero según
las reglas de tiempo y de transición del controlador.

Las coordenadas se suavizan mediante una media móvil exponencial:

```text
filtered = (1 − alpha) × previous_filtered + alpha × measured
alpha = 0.65
```

`filtered` es la posición filtrada, `previous_filtered` es su valor anterior
y `measured` es la medición actual. El primer objetivo válido inicializa el
filtro directamente. Un valor mayor de alpha da más peso a la medición nueva;
un valor menor aumenta el suavizado y el retraso. El filtro reduce las
oscilaciones, pero no elimina la latencia de cámara e inferencia.

### 8.2 Error independiente de la resolución

El centro de la cámara es `(width / 2, height / 2)`. Las constantes del
control se ajustaron con coordenadas de 640×480, por lo que los errores se
normalizan:

```text
error_x = (target_x − width / 2) × 640 / width
error_y = (target_y − height / 2) × 480 / height
```

La misma normalización se aplica a las posiciones medidas y filtradas.
Así, un objetivo en la misma posición relativa del fotograma no genera una
corrección mayor únicamente porque aumentó la resolución. Un cambio de campo
de visión sí puede modificar la relación entre el movimiento de la escena
y los píxeles.

### 8.3 Zona muerta, ganancia proporcional y límite de paso

`calculate_axis_step()` devuelve cero si el error medido actual está a
10 píxeles de referencia o menos del centro, o si los errores medido y
filtrado tienen signos opuestos. Esta segunda condición evita que el
historial del filtro siga moviendo la cámara en la dirección anterior
cuando el objetivo ya cruzó el centro.

En otro caso, `calculate_step()` aplica:

```python
step = int(0.035 * filtered_error)
step = max(-3, min(3, step))
```

También aplica la zona muerta al error filtrado. `int()` trunca hacia cero,
por lo que algunos errores pequeños, aunque estén fuera de los 10 píxeles,
todavía producen un paso cero. Es un controlador proporcional limitado y
suavizado, **no un PID**: no contiene término integral ni derivativo.

### 8.4 Ejemplo numérico y dirección

Supongamos un primer objetivo en `(840, 450)` dentro de una imagen de 1280×720:

```text
Centro:                     (640, 360)
Error en píxeles originales: (200, 90)
Error normalizado:          (100, 60)
Paso proporcional entero:   (3, 2)
Signos en Python:           PAN_SIGN=+1, TILT_SIGN=−1
Mensaje serial:             "3,-2\n"
```

El firmware ejecuta después `panPos -= dx` y `tiltPos += dy`. Desde los
ángulos iniciales `(80, 100)`, ese mensaje solicita `(77, 98)`. La dirección
depende tanto de los signos en Python como del montaje físico de los servos;
cambiar solo una parte puede invertir el seguimiento.

### 8.5 Temporización y parada

Las correcciones normales se envían con una separación mínima de 0.05
segundos. La desactivación de un objetivo o el cambio de un eje a cero puede
provocar un `0,0` anticipado. Esto evita que una parada repita el movimiento
del otro eje. La frecuencia real puede ser menor si cada fotograma tarda
más en procesarse.

El firmware interpreta cada pareja como un incremento de ángulo aplicado
una sola vez, no como una velocidad continua. `0,0` significa que el ángulo
no cambia; no desconecta los servos ni los manda a Home.
`MotionController.stop()` también limpia el suavizado y el estado temporal.

## 9. Protocolo serial y Home

Python usa `pyserial` a 115200 baudios y escribe líneas ASCII terminadas con
un salto de línea. No espera confirmaciones del Arduino.

| Mensaje | Comportamiento del firmware |
| --- | --- |
| `3,-2\n` | Restar 3 a pan y sumar −2 a tilt; limitar después los ángulos a 0–180°. |
| `0,0\n` | Mantener los ángulos almacenados. |
| `H\n` | Acercarse a pan 80° y tilt 100°, con un máximo de 2° por eje. |

El programa de Arduino conecta pan al pin 9 y tilt al pin 10. Al arrancar
escribe los ángulos iniciales. Python retrasa la apertura del puerto serial
hasta que alguna función de movimiento lo necesita, evitando reiniciar el
Arduino solamente por visualizar la cámara en Static.

`TrackingService.home()` cambia a Static, deshabilita el seguimiento,
incrementa `revision` y programa 50 comandos `H`. El hilo envía como máximo
uno por iteración, con al menos 50 ms entre comandos. La distancia máxima
desde los límites de 0–180° hasta cualquiera de los ángulos iniciales es
100°; por eso 50 comandos de 2° cubren todo el recorrido.
`approach()` limita cada paso al objetivo, de modo que comandos adicionales
no sobrepasan la posición inicial.

Home tarda aproximadamente 2.5 segundos o más, según la duración del ciclo
y el arranque serial. **Stop session** cancela los comandos pendientes.
No hay lectura de un codificador de posición ni confirmación del firmware:
la finalización indica que se envió la secuencia, no una llegada física medida.

## 10. Flask, transmisión al navegador y estado

Las funciones que atienden peticiones Flask no capturan fotogramas. Llaman al
servicio compartido, cuyo hilo administra el hardware. `settings` almacena
los controles solicitados; `state` guarda la fase, conexiones, dimensiones
medidas, FPS y errores. `snapshot()` combina ambos para el navegador.

| Ruta | Propósito |
| --- | --- |
| `GET /` | Generar la interfaz desde la plantilla e incluir el token de control. |
| `GET /api/status` | Leer el estado compartido actual. |
| `GET /api/cameras` | Enumerar cámaras físicas. |
| `POST /api/session/start` | Iniciar el hilo de trabajo. |
| `POST /api/session/stop` | Solicitar la parada y liberación de recursos. |
| `POST /api/settings` | Cambiar modo, seguimiento, salida virtual, vista previa o pinza. |
| `POST /api/camera` | Seleccionar una cámara física. |
| `POST /api/home` | Programar el regreso a la posición inicial. |
| `GET /stream.mjpg` | Transmitir la última vista previa codificada como JPEG multipartes. |

`app.js` consulta el estado aproximadamente cada 750 ms después de terminar
una petición. `command()` envía JSON con `X-Control-Token`, evita comandos
locales simultáneos y actualiza los controles desde el estado confirmado
por el servidor. F/H/S son atajos del navegador: **H en el navegador significa
Hand**, mientras que **H en el puerto serial significa Home**.

Para el video, `_frame()` codifica bytes JPEG e incrementa `sequence`.
El generador `frames()` espera mediante una `Condition` hasta que cambie
la secuencia o transcurran dos segundos; después produce una parte JPEG.
Un navegador lento recibe el último fotograma almacenado, no una cola
ilimitada de imágenes antiguas. Es MJPEG sobre HTTP, no WebRTC ni una ventana
gráfica de OpenCV.

**Hide preview** elimina la fuente de imagen del navegador y omite las
siguientes codificaciones JPEG en el hilo. La detección, captura y salida
virtual siguen activas. La implementación todavía crea la copia de vista
previa y dibuja sus marcadores, por lo que no elimina todo el trabajo
relacionado con la visualización. Ocultarla afecta a todas las pestañas,
porque el ajuste pertenece a la sesión compartida.

Las rutas POST validan un token de control y rechazan encabezados Origin
que no coincidan. Flask también restringe los hosts aceptados y escucha
solo en la dirección local. Son protecciones para el control local, no
un sistema de cuentas e inicio de sesión para publicar la app en Internet.

## 11. Concurrencia, detección de cámaras y persistencia

Una `threading.Condition` basada en `RLock` protege la configuración y el
estado compartidos. La inferencia se ejecuta fuera del bloqueo para que un
modelo lento no detenga todas las peticiones HTTP. Antes de enviar una
corrección serial, el hilo compara la `revision` guardada con la actual.
Una pausa, Home o un cambio de modo durante la inferencia invalida el
resultado anterior.

`threading.Event` comunica las solicitudes de parada. Un bloque `finally`
intenta detener el controlador y cerrar la salida virtual, el puerto serial,
MediaPipe y la cámara, incluso si la inicialización falló a medio camino.
Los errores de salida virtual se manejan por separado para permitir que la
detección y vista previa continúen si solo falla esa salida. Los errores
de cámara o serial detienen el hilo y se muestran en el estado.

`camera_devices.list_cameras()` consulta las capacidades de V4L2 sin iniciar
la captura. Excluye entradas que solo proporcionan metadatos y dispositivos
virtuales loopback. Seleccionar otra cámara pausa el seguimiento, libera
la fuente anterior y recrea la salida virtual con el nuevo tamaño si estaba
habilitada.

Los cambios de pinza se validan y guardan en `config/local-settings.json`.
`local_settings.py` escribe un archivo temporal, vacía y sincroniza los
búferes y reemplaza el archivo de configuración de forma atómica. Si falla
el guardado, el umbral en memoria no cambia. Si el archivo falta o contiene
datos inválidos, se usa 0.20.

Solo se conserva la pinza entre ejecuciones. Al iniciar el proceso se usa
Static, con movimiento y salida apagados y vista previa encendida. Detener
y reiniciar una sesión dentro del mismo proceso puede conservar otras
selecciones en memoria; no equivale a reiniciar toda la aplicación.

## 12. Rendimiento, observaciones y limitaciones

La métrica `fps` cuenta los fotogramas completados por el hilo en intervalos
de aproximadamente medio segundo. `inference_ms` incluye la reducción de
imagen, la llamada al modelo seleccionado, la extracción del objetivo y sus
marcadores. Excluye la codificación JPEG, la salida virtual y la mayor parte
de la captura. Ningún valor mide el retraso completo de cámara a pantalla o
de cámara a servo.

En una comprobación local, la app informó aproximadamente 21.6 FPS y 36.7 ms
en modo Face, mientras la captura física y la salida virtual eran de
1280×720. Fue una observación puntual, no una medición controlada ni una
promesa de rendimiento.

Reducir la resolución de inferencia disminuye el trabajo, pero puede perder
detalles pequeños. Capturar en HD sigue requiriendo decodificación, copias
de memoria, codificación y ancho de banda de salida. No hay hilos separados
para captura e inferencia ni una frecuencia constante garantizada para la
cámara virtual. El búfer pequeño solicitado reduce la posible acumulación
de imágenes donde se admite, pero no todos los controladores de cámara
necesariamente respetan ese ajuste.

Otras limitaciones son los dedos ocultos, las palmas giradas, el cambio de
rostro objetivo, la ganancia fija y el accionamiento de servos sin lectura
de su posición. Si desaparece el objetivo se mantiene la posición; no se
regresa a Home automáticamente. Tener un dispositivo virtual HD tampoco
garantiza que otro participante reciba HD, porque la aplicación de
videollamadas controla su procesamiento y transmisión.

## 13. Pruebas y demostración

Desde la raíz del repositorio, ejecuta:

```bash
.venv/bin/python -B -m unittest discover -s tests -v
```

La suite actual contiene 34 pruebas distribuidas en dos archivos:

| Área | Ejemplos de lo que se comprueba |
| --- | --- |
| Objetivos y coordenadas | Rostro más grande, conversión de rectángulos a HD y puntos de mano válidos. |
| Pinza | Invariancia frente a cambios uniformes de tamaño, histéresis y referencias inválidas o ausentes. |
| Movimiento | Mismas correcciones a distintas resoluciones, parada ante historial desactualizado y secuencias seriales. |
| HTTP y ciclo de vida | Validación, token/origen, Static, restricciones de preview-only e inicio/parada. |
| Home | Cantidad y separación de comandos, pausa durante inferencia y cobertura de los límites angulares. |
| Salidas | Fotogramas virtuales limpios, manejo de fallos y omisión de JPEG al ocultar la vista previa. |
| Persistencia y limpieza | Restauración de pinza, fallo de guardado, inicialización parcial y liberación de recursos. |

Las pruebas usan imágenes sintéticas y dispositivos simulados; no necesitan
mover un Arduino. Verifican algoritmos y comportamiento de integración,
no la precisión de los modelos, la calidad mecánica del seguimiento ni una
conexión real de videollamada.

Para demostrar el hardware, inicia en Static y muestra la vista HD. Después
selecciona Face, muévete lentamente por la imagen y explica el error respecto
al centro. Selecciona Hand y muestra que los dedos abiertos no mueven la
cámara; realiza la pinza para habilitar el seguimiento. Demuestra que Home
regresa a Static y oculta la vista previa mientras continúa la salida
virtual. Termina con **Stop session**.

## 14. Guion sugerido para explicar al profesor

Una exposición de seis a ocho minutos puede seguir este orden:

1. **Problema y resultado:** mostrar la cámara siguiendo un objetivo.
2. **Procesamiento con OpenCV:** enseñar `camera.read()`, la forma del arreglo
   NumPy, `raw.copy()` y `tracking_frame()`. Explicar por qué el video y la
   inferencia usan tamaños distintos.
3. **Detección y lógica propia:** mostrar las llamadas a los modelos y después
   los cálculos de selección de rostro y de pinza en `tracking_targets.py`.
4. **Matemáticas del movimiento:** desarrollar el ejemplo `(840, 450)` y
   enseñar `MotionController.update()` junto con la resta y suma del Arduino.
5. **Interfaz y concurrencia:** mostrar una ruta Flask y explicar por qué el
   ciclo de hardware vive en un hilo, con revisión del estado antes de mover.
6. **Pruebas y limitaciones:** ejecutar las pruebas, explicar lo que las
   simulaciones no demuestran y comentar iluminación, latencia y ausencia
   de medición de posición.

Preguntas útiles para ensayar:

| Pregunta | Respuesta basada en esta implementación |
| --- | --- |
| ¿Construiste o entrenaste YOLO/MediaPipe? | No. El proyecto integra modelos preentrenados e implementa el sistema de control que los utiliza. |
| ¿Para qué OpenCV si YOLO detecta los rostros? | OpenCV proporciona captura, transformaciones de imagen, marcadores y codificación de vista previa. |
| ¿Por qué se usan dos tamaños de imagen? | Para conservar la calidad de salida y limitar el tamaño de entrada a la inferencia. |
| ¿Es un PID? | No. Usa correcciones proporcionales enteras, suavizado exponencial, zona muerta y límites de paso. |
| ¿Por qué una pinza relativa? | Una proporción compensa cambios uniformes del tamaño aparente de la mano. |
| ¿Por qué Python no envía ángulos absolutos? | El firmware mantiene los ángulos de los servos; Python envía correcciones relativas pequeñas. |
| ¿Home confirma la posición física? | No. Envía suficientes comandos de aproximación para cubrir el rango angular permitido por el firmware. |
| ¿Por qué Flask en lugar de imshow? | El navegador reúne video, configuración y controles de salida mientras Python mantiene un solo hilo de trabajo compartido. |
| ¿Qué mejorarías después? | Medir la latencia completa, separar captura e inferencia si las mediciones lo justifican y añadir realimentación o confirmaciones antes de afirmar que se mide la posición. |

Una descripción breve para practicar:

> Mi programa de Python usa OpenCV para adquirir y preparar imágenes,
> modelos preentrenados para localizar un objetivo y lógica de control propia
> para convertir su desplazamiento respecto al centro de la imagen en
> correcciones seriales para los servos. Flask proporciona la interfaz,
> mientras una ruta de imagen limpia alimenta la cámara virtual.
