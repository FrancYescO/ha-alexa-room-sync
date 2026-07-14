# Alexa Room Sync

Custom integration sperimentale per sincronizzare le aree di Home Assistant
con i gruppi/stanza Alexa degli endpoint esposti, per esempio, da Home
Assistant Matter Hub.

> [!WARNING]
> Usa endpoint GraphQL privati osservati nell'app Alexa a luglio 2026. Non è
> un'API pubblica Amazon: può cambiare e il cookie di sessione scadrà. Esegui
> sempre `alexa_room_sync.preview` prima di `alexa_room_sync.apply`.

## Protezioni incluse

- Crea soltanto i gruppi Alexa mancanti richiesti da associazioni univoche.
- Non rinomina né elimina gruppi Alexa.
- Gestisce solo gruppi il cui nome coincide con un'area Home Assistant.
- Non modifica gruppi Alexa estranei alle aree HA.
- Blocca nomi Alexa duplicati e mapping su più aree.
- Applica prima le rimozioni e poi le aggiunte, una richiesta alla volta.
- Rilegge endpoint e gruppi immediatamente prima di ogni applicazione.
- Le azioni avviate da un utente richiedono un amministratore HA.

## Installazione

1. Copia `custom_components/alexa_room_sync` nella directory
   `/config/custom_components/` di Home Assistant.
2. Riavvia Home Assistant.
3. Apri **Impostazioni → Dispositivi e servizi → Aggiungi integrazione** e
   cerca **Alexa Room Sync**.

## Configurazione della sessione

Nel HAR fornito, la richiesta utile è:

```text
POST https://eu-api-alexa.amazon.it/nexus/v1/graphql
operationName: UpdateDeviceGroup
```

Nel flusso di configurazione inserisci:

- **Host**: `https://eu-api-alexa.amazon.it`
- **Cookie**: il valore completo dell'header `Cookie` di una richiesta
  `/nexus/v1/graphql` recente.
- **Header aggiuntivi**: normalmente `{}` è sufficiente. Se Alexa rifiuta la
  richiesta, inserisci come JSON soltanto gli header `x-amzn-*` presenti nella
  stessa richiesta. Non inserire `Cookie`, `Host`, `Content-Length`, `Accept`
  o `Content-Type`: vengono gestiti dal componente.
- **Modelli endpoint**: il valore resta disponibile per compatibilità. La
  sincronizzazione considera tutti gli endpoint controllabili e scarta client
  Alexa, app e hub; modifica soltanto quelli con un nome HA univoco.
- **Mapping manuali**: inizialmente `{}`.

Il cookie è una credenziale: non pubblicare il HAR e non allegarlo a issue o
log. Home Assistant lo memorizza nella propria configurazione interna, come le
altre credenziali delle integrazioni.

## Prima esecuzione

Da **Strumenti per sviluppatori → Azioni**, esegui:

```yaml
action: alexa_room_sync.preview
response_variable: preview
```

La risposta contiene:

- `additions` e `removals`: modifiche che verrebbero effettuate;
- `ambiguous`: nomi duplicati o mapping non sicuri;
- `unmatched_alexa`: endpoint Alexa senza corrispondenza HA;
- `missing_alexa_groups`: aree HA prive di un gruppo Alexa omonimo;
- `groups_to_create`: stanze che verranno create automaticamente;
- `pending_additions`: endpoint che saranno aggiunti dopo la creazione stanza;
- `change_count`: totale di creazioni e modifiche previste.

Applica soltanto dopo aver controllato l'anteprima:

```yaml
action: alexa_room_sync.apply
response_variable: result
```

Al termine viene emesso anche l'evento `alexa_room_sync_finished`.

## Mapping manuale dei duplicati

Il mapping è `entity_id` Home Assistant → `endpoint_id` Alexa. Esempio:

```json
{
  "light.lampada_scrivania": "amzn1.alexa.endpoint.00000000-0000-0000-0000-000000000000"
}
```

L'`endpoint_id` compare nella sezione `ambiguous`/`unmatched_alexa`
dell'anteprima. Per modificare i mapping, elimina e riconfigura l'integrazione
in questa versione MVP.

## Criterio di associazione

Il componente confronta, senza distinzione tra maiuscole e accenti:

- nome personalizzato e nome originale dell'entità;
- `friendly_name` dello stato;
- nome personalizzato e nome del device HA;
- nome dell'endpoint Alexa.

Quando MatterHub pubblica l'`entity_id` HA nel numero seriale Alexa, il
componente usa direttamente questa identità stabile e non dipende dal nome.

L'area effettiva è prima quella assegnata all'entità, altrimenti quella del
device. Un match è automatico solo quando conduce a un singolo endpoint e a
una singola area.

## Ripristino e limiti

La versione MVP non mantiene un backup separato: l'anteprima restituita da HA
va salvata prima dell'applicazione. La mutation `REMOVE` è la controparte
simmetrica della mutation `ADD` catturata nel HAR. Se Amazon cambia schema o
autenticazione, il componente interrompe l'operazione e non tenta endpoint
alternativi.
