# Eieragenda

Eieragenda als echte Home Assistant add-on met Ingress.

## Wat doet deze add-on?

- Draait de Eieragenda rechtstreeks in Home Assistant.
- Toont de webapp via Home Assistant Ingress.
- Werkt via je normale Home Assistant login en HTTPS.
- Publiceert geen aparte poort voor de Eieragenda.
- Database staat in `/share/eieragenda/eieragenda.db`.
- App-bestanden staan in `/share/eieragenda/app/`.

## Aanpassingen doen

Na de eerste start kun je via Samba/File Editor werken in:

```text
/share/eieragenda/app/
```

Daar staan onder andere:

```text
app.py
templates/
static/
requirements.txt
```

Voor kleine aanpassingen vervang je daar de bestanden en herstart je daarna de add-on.

## Database

De database staat hier:

```text
/share/eieragenda/eieragenda.db
```

Stop de add-on voordat je de database handmatig bewerkt.
