class DataSourceError(Exception):
    """A public data source failed or answered something unusable."""


class ParcelNotFound(DataSourceError):
    """The cadastral reference does not exist, or is not served by the state Catastro.

    The Basque Country and Navarra keep their own foral cadastres, out of scope for the
    MVP: parcels there fail here and the order is refunded.
    """


class GeometryUnavailable(DataSourceError):
    """The INSPIRE WFS returned no geometry for the parcel."""


class ParcelNotRustic(DataSourceError):
    """La referencia existe, pero es urbana.

    Todo el producto está construido sobre cartografía rústica —subparcelas de cultivo,
    vías pecuarias, montes de utilidad pública, NDVI— y sobre una pregunta que solo tiene
    sentido en el campo: qué afecciones arrastra la finca. Sobre un piso, el informe se
    generaría igual y no diría nada útil. Se rechaza antes de cobrar, no después.
    """


class ParcelNotCovered(DataSourceError):
    """La parcela está en un catastro foral, fuera del Catastro común.

    Álava, Gipuzkoa, Bizkaia y Navarra mantienen catastro propio y no se publican en los
    servicios del Estado. Se detecta por el código de provincia de la referencia rústica,
    antes de preguntar al OVC: así el usuario recibe el motivo real en lugar del «no existe
    ningún inmueble» que devolvería el servicio estatal, que suena a error de tecleo.
    """
