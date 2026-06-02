# Eieragenda Home Assistant Add-on repository

Deze repository bevat de Home Assistant add-on **Eieragenda**.

De add-on draait de Eieragenda als echte Home Assistant add-on met Ingress. Daardoor kun je hem via Home Assistant openen, ook buitenshuis via je normale Home Assistant HTTPS/Nabu Casa verbinding, zonder de Eieragenda zelf publiek te maken.

## Installatie via GitHub

1. Maak een nieuwe GitHub repository, bijvoorbeeld `eieragenda-ha-addon`.
2. Upload de inhoud van deze map naar die repository.
3. Ga in Home Assistant naar **Instellingen → Add-ons → Add-onwinkel → ⋮ → Repositories**.
4. Voeg de GitHub URL toe.
5. Installeer de add-on **Eieragenda**.
6. Start de add-on en zet **Toon in zijbalk** aan.

## Database overzetten

De database staat in de add-on op `/data/eieragenda.db` en gaat mee met Home Assistant backups.

Wil je je oude database overzetten, zet dan je bestaande `eieragenda.db` eerst in de Home Assistant `/share` map. Als de optie `restore_from_share` aan staat en er nog geen database in de add-on bestaat, kopieert de add-on automatisch `/share/eieragenda.db` naar `/data/eieragenda.db` bij de eerste start.

## Belangrijk

Deze add-on is gemaakt op basis van de laatste stabiele v47-basis van de Eieragenda, niet op basis van de experimentele v49/v50 mobiele zoekaanpassingen.


Databasepad: `/share/eieragenda/eieragenda.db`
