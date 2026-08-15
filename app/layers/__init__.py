"""Reference layers bulk-loaded into PostGIS (flood zones, Natura 2000, MUP...).

No router: nothing here is exposed over HTTP. It is the one domain other domains are
allowed to import, because "which polygons cover this parcel" is a question the reports
domain cannot answer for itself. Loading is done out of band with ogr2ogr
(scripts/load_layers.sh); the app only reads.
"""
