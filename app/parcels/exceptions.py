class ParcelError(Exception):
    """Base error for the parcels domain."""


class ParcelUnavailable(ParcelError):
    """The parcel cannot be processed: unknown reference, foral cadastre or no geometry.

    Orders that end here are refunded in full — the frontend promises exactly that.
    """
