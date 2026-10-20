"""Cloudflare Worker entry point for the public, read-only Data90 web app."""

from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

from workers import Response, WorkerEntrypoint, patch_env, wsgi

from data90.web import FootballAnalyticsWeb

application = FootballAnalyticsWeb(enable_sync=False)
_request_lock = asyncio.Lock()


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        if request.method.upper() != "GET":
            return Response("Solo lectura", status=405)

        request_url = urlsplit(request.url)
        if request_url.path in {"/api/sync", "/api/sync/status"}:
            return Response("No encontrado", status=404)

        try:
            database_url = self.env.HYPERDRIVE.connectionString
        except AttributeError:
            return Response("Servicio temporalmente no disponible", status=503)
        if database_url.startswith("postgres://"):
            database_url = "postgresql+psycopg://" + database_url.removeprefix("postgres://")
        elif database_url.startswith("postgresql://"):
            database_url = "postgresql+psycopg://" + database_url.removeprefix("postgresql://")

        allowed_host = request_url.netloc.split(":", 1)[0].casefold()
        async with _request_lock:
            with patch_env(
                DATA90_WEB_DATABASE_URL=database_url,
                DATA90_WEB_ALLOWED_HOSTS=allowed_host,
                DATA90_WEB_ALLOW_REMOTE="1",
            ):
                return await wsgi.fetch(application, request, self.env)
