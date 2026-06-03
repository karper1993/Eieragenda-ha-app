# Eieragenda Home Assistant Add-on repository

Deze repository bevat de Home Assistant add-on **Eieragenda**.

De add-on draait de Eieragenda als echte Home Assistant add-on met Ingress. Daardoor kun je hem via Home Assistant openen, ook buitenshuis via je normale Home Assistant HTTPS/Nabu Casa verbinding, zonder de Eieragenda zelf publiek te maken.

## Installatie via GitHub

1. Upload de inhoud van deze map naar je GitHub repository.
2. Ga in Home Assistant naar **Instellingen → Add-ons → Add-onwinkel → ⋮ → Repositories**.
3. Voeg de GitHub URL toe.
4. Installeer of update de add-on **Eieragenda**.
5. Start de add-on en zet **Toon in zijbalk** aan.

## Database en app-bestanden

Deze versie gebruikt deze paden:

```text
/share/eieragenda/eieragenda.db
/share/eieragenda/app/
```

Bij de eerste start kopieert de add-on de ingebouwde app-bestanden automatisch naar:

```text
/share/eieragenda/app/
```

Daarna kun je kleine aanpassingen doen door bestanden in die map te vervangen en de add-on opnieuw te starten. Voor gewone webapp-aanpassingen hoef je dan niet steeds GitHub te updaten.

## Belangrijk

Deze add-on is gemaakt op basis van de stabiele v47-basis van de Eieragenda, niet op basis van de experimentele v49/v50 mobiele zoekaanpassingen.


## Home Assistant notificatie

Deze versie kan bij nieuwe, gewijzigde en voltooide bestellingen `input_text.eieragenda_notificatie` bijwerken. Maak deze helper eerst aan in Home Assistant.
