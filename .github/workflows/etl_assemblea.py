name: ETL sedute Assemblea (Camera)

on:
  schedule:
    - cron: "30 4 * * *"
    - cron: "0 14 * * *"
  workflow_dispatch:
    inputs:
      modalita:
        description: "Cosa vuoi fare"
        required: true
        default: "normale"
        type: choice
        options:
          - normale
          - introspezione
          - completo

permissions:
  contents: write

concurrency:
  group: etl-assemblea
  cancel-in-progress: false

jobs:
  aggiorna:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      # Controllo prima di tutto che il file ci sia: se manca, dirlo chiaramente
      # invece di lasciare un errore incomprensibile.
      - name: Controlla che lo script sia al suo posto
        run: |
          if [ ! -f "etl_assemblea.py" ]; then
            echo "::error::Il file etl_assemblea.py NON si trova nella cartella principale del repository."
            echo ""
            echo "Ecco cosa c'è invece nella cartella principale:"
            ls -la
            echo ""
            echo "COSA FARE: crea il file 'etl_assemblea.py' nella pagina principale"
            echo "del repository (Add file > Create new file), non dentro una sottocartella."
            echo "Il nome deve essere esattamente etl_assemblea.py, tutto minuscolo."
            exit 1
          fi
          echo "Script trovato."

      - name: Installa le librerie necessarie
        run: pip install --quiet requests cloudscraper

      - name: Mostra i nomi reali dei campi
        if: github.event.inputs.modalita == 'introspezione'
        run: |
          echo "=================================================="
          echo " NOMI DEI CAMPI USATI DALLA CAMERA"
          echo " Copia questo risultato e mandalo a chi sviluppa."
          echo "=================================================="
          python etl_assemblea.py --introspect

      - name: Scarica le sedute
        id: etl
        if: github.event.inputs.modalita != 'introspezione'
        run: |
          set +e
          if [ "${{ github.event.inputs.modalita }}" = "completo" ]; then
            python etl_assemblea.py --full
          else
            python etl_assemblea.py
          fi
          CODICE=$?
          echo "codice=$CODICE" >> "$GITHUB_OUTPUT"
          exit 0          # non blocco il workflow: interpreto il codice dopo
        continue-on-error: true

      - name: Spiega cosa è successo
        if: github.event.inputs.modalita != 'introspezione'
        run: |
          C="${{ steps.etl.outputs.codice }}"
