class DataSourceError(Exception):
    """A public data source failed or answered something unusable."""


class ParcelNotFound(DataSourceError):
    """The cadastral reference does not exist, or is not served by the state Catastro.

    The Basque Country and Navarra keep their own foral cadastres, out of scope for the
    MVP: parcels there fail here and the order is refunded.
    """


class GeometryUnavailable(DataSourceError):
    """The INSPIRE WFS returned no geometry for the parcel."""
