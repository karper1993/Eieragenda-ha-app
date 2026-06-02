# Eieragenda

Eieragenda als echte Home Assistant add-on met Ingress.

## Wat doet deze add-on?

- Draait de Eieragenda rechtstreeks in Home Assistant.
- Toont de webapp via Home Assistant Ingress.
- Werkt dus via je normale Home Assistant login en HTTPS.
- Publiceert geen aparte poort voor de Eieragenda.
- Database staat in `/data/eieragenda.db`, zodat Home Assistant backups hem meenemen.

## Oude database importeren

1. Zet je bestaande `eieragenda.db` in de Home Assistant `/share` map.
2. Zorg dat de add-on optie `restore_from_share` op `true` staat.
3. Start de add-on.
4. Alleen als `/data/eieragenda.db` nog niet bestaat, wordt `/share/eieragenda.db` automatisch gekopieerd.

Daarna kun je `restore_from_share` eventueel uitzetten.


Databasepad: `/share/eieragenda/eieragenda.db`
