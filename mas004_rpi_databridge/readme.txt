Config.json mit Token liegt in:
sudo nano /etc/mas004_rpi_databridge/config.json

Token: siehe /etc/mas004_rpi_databridge/config.json

Raspi Dienst neu starten:
sudo systemctl restart mas004-rpi-databridge.service
sudo journalctl -u mas004-rpi-databridge.service -f

URL für Testumgebung:
http://192.168.1.100:8080/ui/test

URL für Databridge:
http://192.168.1.100:8080/

URL für Parameterbearbeitung:
http://192.168.1.100:8080/ui/params

URL für API Doku:
http://192.168.1.100:8080/docs

Mikrotom Testtool Starten au Power Shell:
python "D:\Users\Egli_Erwin\Veralto\DE-SMD-Support-Switzerland - Documents\26_VS_CODE\SAR41-MAS-004_Roche_LSR_TTO\Raspberry-PLC\Mikrotom-Simulator\mikrotom_sim.py"
