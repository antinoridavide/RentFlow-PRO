# RentFlow PRO — crea l'APK dal telefono con GitHub Actions

Questo progetto è predisposto per compilare automaticamente un APK Android installabile senza PC.

## Cosa contiene
- App Android con WebView.
- Python 3.11 integrato con Chaquopy.
- RentFlow Flask eseguito solo su `127.0.0.1:5000` dentro l'app.
- Database SQLite, firme e foto nella memoria privata dell'app.
- Accesso Internet per CaRGOS.
- Workflow GitHub Actions che genera `RentFlow-PRO.apk`.

## Procedura dal telefono
1. Crea o accedi a un account GitHub.
2. Crea un nuovo repository, ad esempio `RentFlow-PRO-Android`.
3. Carica **tutto il contenuto di questa cartella** nel repository, compresa la cartella nascosta `.github`.
4. Apri la scheda **Actions** del repository.
5. Apri **Build RentFlow APK** e premi **Run workflow**.
6. Attendi che il lavoro mostri il segno verde di completamento.
7. Apri l'esecuzione terminata e, nella sezione **Artifacts**, scarica `RentFlow-PRO-APK`.
8. Estrai lo ZIP scaricato: dentro trovi `RentFlow-PRO.apk`.
9. Tocca l'APK sul Samsung e autorizza, se richiesto, l'installazione di app da quella sorgente.

## Nota sulla firma
Il workflow crea un APK **debug**, firmato automaticamente dagli strumenti Android e installabile direttamente sul telefono. È adatto per uso personale/test. Per Play Store o distribuzione definitiva è necessaria una chiave di firma release da conservare in modo sicuro.

## Nota sui dati
Disinstallare l'app può eliminare il database e i file presenti nella memoria privata dell'app. Prima dell'uso operativo è raccomandata una funzione di esportazione/backup.
