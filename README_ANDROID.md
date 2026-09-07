# RentFlow PRO — Android

Questa cartella contiene la conversione Android del progetto Flask RentFlow PRO.

## Architettura
- Android WebView come interfaccia nativa contenitore.
- Python 3.11 incorporato tramite Chaquopy.
- Flask avviato solo su `127.0.0.1:5000`, quindi non è esposto alla rete Wi-Fi.
- Database SQLite, firme e foto vengono salvati nella memoria privata dell'app Android.
- L'accesso Internet è abilitato per l'integrazione CaRGOS.

## Compilazione APK con Android Studio
1. Apri questa cartella in Android Studio.
2. Attendi la sincronizzazione Gradle e il download delle dipendenze.
3. Se richiesto, installa Android SDK 35.
4. Menu **Build > Build App Bundle(s) / APK(s) > Build APK(s)**.
5. Per una versione definitiva da distribuire: **Build > Generate Signed App Bundle / APK** e crea/conserva una chiave di firma.

## Dati
Disinstallare l'app elimina normalmente i dati privati dell'app. Prima dell'uso reale è consigliato aggiungere una funzione di backup/esportazione del database.

## Sicurezza
Le credenziali CaRGOS del progetto originale sono memorizzate nel database locale. Prima dell'uso reale è consigliato proteggerle con Android Keystore o cifratura equivalente.

## Compilazione senza PC (GitHub Actions)
Il progetto include `.github/workflows/build-apk.yml`. Caricando l'intero progetto su GitHub, il workflow **Build RentFlow APK** produce un APK debug installabile come artifact. Vedi `README_GITHUB_PHONE.md`.
