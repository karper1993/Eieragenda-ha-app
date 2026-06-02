# Eieragenda v47 ingress

Herstelversie:
- Teruggezet naar de laatste werkende basis v42.
- Nieuwe bestelling aanmaken werkt weer via de normale overzichtspagina.
- Herhaal laatste gaat weer via de overzichtspagina met repeat_customer_id.
- V46-wijziging met extra modal op Klanten is verwijderd, omdat die Nieuwe bestelling stuk maakte.

Update:
sudo systemctl stop eieragenda
cp ~/Eieragenda/eieragenda.db ~/eieragenda-backup-$(date +%F-%H%M).db
cd ~/Downloads
unzip Eieragenda_v47_ingress.zip
cp -r Eieragenda_v47_ingress/* ~/Eieragenda/
sudo systemctl start eieragenda
sudo systemctl status eieragenda
