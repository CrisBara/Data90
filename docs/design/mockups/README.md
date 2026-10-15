# Propuestas visuales de Data 90

## Nuevas direcciones modernas

[Abrí la nueva galería](modern-round/index.html) para comparar tres composiciones originales. Conservan la fuente Segoe UI y los azules/celestes del ejecutable, pero no reutilizan su distribución: una propuesta editorial de jornada, otra centrada en el calendario y una tercera como cuaderno de competición. Incluyen tablas, partidos, ficha de equipo y muestra de jugadores; todos los datos y los monogramas son de demostración. No alteran la interfaz de producción ni la base de datos.

## Segunda ronda: composiciones distintas

[Abrí la segunda ronda](modern-round-2/index.html): **Señal de jornada** organiza los cruces, resultados y apuntes como una transmisión modular; **Mapa de liga** convierte la clasificación en un mapa de tendencias y distancias; **Explorador global** prioriza la búsqueda y comparación de clubes y jugadores, sin una pestaña independiente de Jugadores. Mantienen Segoe UI y la paleta azul/celeste del ejecutable, con navegación y retículas diferentes a las de la primera ronda. Los datos son de muestra y la galería no modifica producción.

## Propuesta aplicada: pizarra híbrida 01 + 03

[Abrí el mockup híbrido](hybrid/index.html). Esta es la referencia visual de la pantalla inicial del ejecutable: barra superior, navegación lateral, partido destacado, tabla de posiciones, agenda del día y resumen semanal. Combina la composición de Pizarra de control con la retícula, cifras y detalles de Blueprint.

El mockup usa datos de demostración. El ejecutable conserva la misma composición, pero obtiene partidos, tablas y conteos de PostgreSQL. No se muestra una sección de Jugadores; sus fichas siguen abriéndose desde búsqueda y planteles.

## Galería de conceptos anteriores

[Abrí la nueva galería web](online/index.html) para comparar tres direcciones dark, diseñadas primero para una futura versión online adaptable a escritorio y móvil. Incluyen calendario con fechas, horarios, jornadas, estados y marcadores; la vista del calendario puede probarse en cada propuesta.

- **Pizarra de control:** mantiene la lectura táctica, con navegación lateral, agenda por día y jerarquía editorial renovada.
- **Crónica de jornada:** centra la experiencia en los partidos y ordena la agenda como una cobertura deportiva editorial.
- **Blueprint de competición:** presenta los cruces como una matriz analítica compacta con tipografía y detalles técnicos.

Cada variante permite recorrer inicio, calendario, competición, ficha de equipo y búsqueda. En móvil la navegación se colapsa y el calendario se reorganiza para lectura vertical. No hay pestañas independientes de Equipos, Jugadores o Herramientas. Las fichas de jugadores se alcanzan desde la búsqueda global o los planteles.

La galería es independiente y no modifica el ejecutable ni la interfaz de producción. La implementación se hará después de que elijas una propuesta.

## Ronda anterior

La [galería anterior](index.html) se conserva para referencia y comparación.

## Escudos y datos de muestra

La aplicación existente está implementada con PySide6/Qt. Estas galerías HTML son prototipos independientes para definir el diseño antes de cambiar la interfaz de producción.

En una consulta de solo lectura a la base local del 7 de octubre de 2026 se encontraron 319 equipos: 182 tienen una URL de escudo (57,1 %) y 137 no tienen URL. Las URL existentes incluyen 95 de `crests.football-data.org`, 54 de Wikimedia, 9 de UEFA y recursos de otros dominios. También hay enlaces de agregadores o miniaturas que requieren revisión antes de tratarlos como fuentes fiables.

La muestra utiliza URL ya guardadas para Arsenal, Manchester City, Liverpool, Real Madrid, Inter, Milan, Aston Villa y Manchester United. La galería no descarga imágenes ni edita el catálogo; el navegador solicita las imágenes a sus URL de origen. Si un origen no responde, muestra “escudo pendiente” en lugar de inventar uno.

Las cifras, fechas, resultados y estadísticas de los mockups son ilustrativos y están identificados como datos de muestra; no provienen de la base real. No se modificaron la base, credenciales ni pantallas de producción.
