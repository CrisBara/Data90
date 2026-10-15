class FootballAnalyticsError(Exception):
    """Base exception for expected application errors."""


class ProviderError(FootballAnalyticsError):
    """A provider request failed or returned an unusable response."""


class ProviderAuthenticationError(ProviderError):
    """Provider credentials were missing or rejected."""


class DatabaseUnavailableError(FootballAnalyticsError):
    """The local PostgreSQL database could not be reached."""


class EntityResolutionError(FootballAnalyticsError):
    """A source entity could not be safely matched to a canonical entity."""
