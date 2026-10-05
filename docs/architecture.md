# Arquitectura

Flujo objetivo:

```text
Providers → Normalizer → Validator / Resolver → Sync Engine → Repositories → PostgreSQL
                                                                        ↓
                                                               Services / Analytics → PySide6
```

La carpeta `domain` contiene tipos canónicos y contratos sin dependencia de ORM ni UI. `providers` conoce protocolos externos y entrega DTOs. La sincronización no debe propagar JSON externo, resolver equipos por fuzzy matching destructivo ni permitir que una fuente secundaria reemplace silenciosamente un dato de mayor prioridad.

`database/models` mantiene el historial, mappings por fuente, snapshots y problemas de calidad. `services` es la entrada de consulta de la UI. `analytics` deriva métricas únicamente de partidos finalizados y datos locales. El botón de actualización de Herramientas ejecuta el servicio de sincronización en un hilo de fondo para mantener receptiva la interfaz.

La configuración usa variables de entorno y `.env` local ignorado por Git. Los secretos se representan como `SecretStr` y no se deben incluir en logs.
